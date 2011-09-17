# coding: utf-8

# general imports
import os, re

# django imports
from django.shortcuts import render_to_response, HttpResponse
from django.template import RequestContext as Context
from django.http import HttpResponseRedirect
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.cache import never_cache
from django.utils.translation import ugettext as _
from django import forms
from django.core.urlresolvers import reverse
from django.dispatch import Signal
from django.core.paginator import Paginator, InvalidPage, EmptyPage
from django.utils.encoding import smart_unicode
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.core.files.base import ContentFile

# filebrowser imports
from filebrowser.settings import *
from filebrowser.functions import get_breadcrumbs, get_filterdate, get_settings_var, handle_file_upload, convert_filename
from filebrowser.templatetags.fb_tags import query_helper
from filebrowser.base import FileListing, FileObject
from filebrowser.decorators import path_exists, file_exists

# PIL import
if STRICT_PIL:
    from PIL import Image
else:
    try:
        from PIL import Image
    except ImportError:
        import Image

# JSON import
try:
    import json
except ImportError:
    from django.utils import simplejson as json

class FileBrowserSite(object):

    def __init__(self, name=None, app_name='filebrowser'):
        self.name = name
        self.app_name = app_name
        self._actions = {}
        self._global_actions = self._actions.copy()
        # Per-site settings:
        self.media_root = MEDIA_ROOT
        self.media_url = MEDIA_URL


    def filebrowser_view(self, view):
        return staff_member_required(never_cache(view))

    def get_urls(self):
        from django.conf.urls.defaults import patterns, url, include    

        urlpatterns = patterns('',
    
            # filebrowser urls (views)
            url(r'^browse/$', path_exists(self, self.filebrowser_view(self.browse)), name="fb_browse"),
            url(r'^createdir/', path_exists(self, self.filebrowser_view(self.createdir)), name="fb_createdir"),
            url(r'^upload/', path_exists(self, self.filebrowser_view(self.upload)), name="fb_upload"),
            url(r'^delete_confirm/$', file_exists(self, path_exists(self, self.filebrowser_view(self.delete_confirm))), name="fb_delete_confirm"),
            url(r'^delete/$', file_exists(self, path_exists(self, self.filebrowser_view(self.delete))), name="fb_delete"),
            url(r'^version/$', file_exists(self, path_exists(self, self.filebrowser_view(self.detail))), name="fb_detail"),
            url(r'^detail/$', file_exists(self, path_exists(self, self.filebrowser_view(self.version))), name="fb_version"),
            # non-views
            url(r'^upload_file/$', csrf_exempt(self._upload_file), name="fb_do_upload"),
            
        )

        return urlpatterns

    def add_action(self, action, name=None):
        """
        Register an action to be available globally.
        """
        name = name or action.__name__
        # Check/create short description
        if not hasattr(action, 'short_description'):
            action.short_description = action.__name__.replace("_", " ").capitalize()
        # Check/create applies-to filter
        if not hasattr(action, 'applies_to'):
            action.applies_to = lambda x: True
        self._actions[name] = action
        self._global_actions[name] = action

    def disable_action(self, name):
        """
        Disable a globally-registered action. Raises KeyError for invalid names.
        """
        del self._actions[name]

    def get_action(self, name):
        """
        Explicitally get a registered global action wheather it's enabled or
        not. Raises KeyError for invalid names.
        """
        return self._global_actions[name]

    def applicable_actions(self, fileobject):
        """
        Return a list of tuples (name, action) of actions applicable to a given fileobject.
        Sorted alphabetically.
        """
        res = []
        for name, action in self.actions:
            if action.applies_to(fileobject):
                res.append((name, action))
        return res

    @property
    def actions(self):
        """
        Get all the enabled actions as a list of (name, func). The list
        is sorted alphabetically by actions names
        """
        res = self._actions.items()
        res.sort(key=lambda name_func: name_func[0])
        return res

    @property
    def urls(self):
        return self.get_urls(), self.app_name, self.name

    def browse(self, request):
        """
        Browse Files/Directories.
        """

        filter_re = []
        for exp in EXCLUDE:
           filter_re.append(re.compile(exp))
        for k,v in VERSIONS.iteritems():
            exp = (r'_%s(%s)') % (k, '|'.join(EXTENSION_LIST))
            filter_re.append(re.compile(exp))

        def filter_browse(item):
            filtered = item.filename.startswith('.')
            for re_prefix in filter_re:
                if re_prefix.search(item.filename):
                    filtered = True
            if filtered:
                return False
            return True
        
        query = request.GET.copy()
        abs_path = u'%s' % os.path.join(self.media_root, query.get('dir', ''))
        
        filelisting = FileListing(abs_path,
            filter_func=filter_browse,
            sorting_by=query.get('o', DEFAULT_SORTING_BY),
            sorting_order=query.get('ot', DEFAULT_SORTING_ORDER),
            media_root=self.media_root,
            media_url=self.media_url,)
        
        files = []
        if SEARCH_TRAVERSE and query.get("q"):
            listing = filelisting.files_walk_filtered()
        else:
            listing = filelisting.files_listing_filtered()
        
        # If we do a search, precompile the search pattern now
        do_search = query.get("q")
        if do_search:
            re_q = re.compile(query.get("q").lower(), re.M)
        
        filter_type = query.get('filter_type')
        filter_date = query.get('filter_date')
        
        for fileobject in listing:
            # date/type filter
            append = False
            if (not filter_type or fileobject.filetype == filter_type) and (not filter_date or get_filterdate(filter_date, fileobject.date or 0)):
                append = True
            # search
            if do_search and not re_q.search(fileobject.filename.lower()):
                append = False
            # append
            if append:
                files.append(fileobject)
        
        filelisting.results_total = len(listing)
        filelisting.results_current = len(files)
        
        p = Paginator(files, LIST_PER_PAGE)
        page_nr = request.GET.get('p', '1')
        try:
            page = p.page(page_nr)
        except (EmptyPage, InvalidPage):
            page = p.page(p.num_pages)
        
        return render_to_response('filebrowser/index.html', {
            'p': p,
            'page': page,
            'filelisting': filelisting,
            'query': query,
            'title': _(u'FileBrowser'),
            'settings_var': get_settings_var(media_root=self.media_root, media_url=self.media_url),
            'breadcrumbs': get_breadcrumbs(query, query.get('dir', '')),
            'breadcrumbs_title': "",
            'media_root': self.media_root,
            'media_url': self.media_url,
        }, context_instance=Context(request, current_app=self.name))
    
    # mkdir signals
    filebrowser_pre_createdir = Signal(providing_args=["path", "name"])
    filebrowser_post_createdir = Signal(providing_args=["path", "name"])

    def createdir(self, request):
        """
        Create Directory.
        """
        from filebrowser.forms import CreateDirForm
        query = request.GET
        abs_path = u'%s' % os.path.join(self.media_root, query.get('dir', ''))
        
        if request.method == 'POST':
            form = CreateDirForm(abs_path, request.POST)
            if form.is_valid():
                abs_server_path = os.path.join(abs_path, form.cleaned_data['name'])
                try:
                    self.filebrowser_pre_createdir.send(sender=request, path=abs_server_path, name=form.cleaned_data['name'])
                    os.mkdir(abs_server_path)
                    os.chmod(abs_server_path, 0775) # ??? PERMISSIONS
                    self.filebrowser_post_createdir.send(sender=request, path=abs_server_path, name=form.cleaned_data['name'])
                    messages.add_message(request, messages.SUCCESS, _('The Folder %s was successfully created.') % form.cleaned_data['name'])
                    redirect_url = reverse("filebrowser:fb_browse", current_app=self.name) + query_helper(query, "ot=desc,o=date", "ot,o,filter_type,filter_date,q,p")
                    return HttpResponseRedirect(redirect_url)
                except OSError, (errno, strerror):
                    if errno == 13:
                        form.errors['name'] = forms.util.ErrorList([_('Permission denied.')])
                    else:
                        form.errors['name'] = forms.util.ErrorList([_('Error creating folder.')])
        else:
            form = CreateDirForm(abs_path)
        
        return render_to_response('filebrowser/createdir.html', {
            'form': form,
            'query': query,
            'title': _(u'New Folder'),
            'settings_var': get_settings_var(media_root=self.media_root, media_url=self.media_url),
            'breadcrumbs': get_breadcrumbs(query, query.get('dir', '')),
            'breadcrumbs_title': _(u'New Folder'),
            'media_root': self.media_root,
            'media_url': self.media_url,
        }, context_instance=Context(request, current_app=self.name))
    

    def upload(self, request):
        """
        Multipe File Upload.
        """
        query = request.GET
        abs_path = u'%s' % os.path.join(self.media_root, query.get('dir', ''))
        
        return render_to_response('filebrowser/upload.html', {
            'query': query,
            'title': _(u'Select files to upload'),
            'settings_var': get_settings_var(media_root=self.media_root, media_url=self.media_url),
            'breadcrumbs': get_breadcrumbs(query, query.get('dir', '')),
            'breadcrumbs_title': _(u'Upload'),
            'media_root': self.media_root,
            'media_url': self.media_url,
        }, context_instance=Context(request, current_app=self.name))

    def delete_confirm(self, request):
        """
        Delete existing File/Directory.
        """
        query = request.GET
        abs_path = u'%s' % os.path.join(self.media_root, query.get('dir', ''))
        fileobject = FileObject(os.path.join(abs_path, query.get('filename', '')), media_root=self.media_root, media_url=self.media_url)
        if fileobject.filetype == "Folder":
            filelisting = FileListing(os.path.join(abs_path, fileobject.filename),
                sorting_by=query.get('o', 'filename'),
                sorting_order=query.get('ot', DEFAULT_SORTING_ORDER),
                media_root=self.media_root,
                media_url=self.media_url,)
            filelisting = filelisting.files_walk_total()
            if len(filelisting) > 100:
                additional_files = len(filelisting) - 100
                filelisting = filelisting[:100]
            else:
                additional_files = None
        else:
            filelisting = None
            additional_files = None
        
        return render_to_response('filebrowser/delete_confirm.html', {
            'fileobject': fileobject,
            'filelisting': filelisting,
            'additional_files': additional_files,
            'query': query,
            'title': _(u'Confirm delete'),
            'settings_var': get_settings_var(media_root=self.media_root, media_url=self.media_url),
            'breadcrumbs': get_breadcrumbs(query, query.get('dir', '')),
            'breadcrumbs_title': _(u'Confirm delete'),
            'media_root': self.media_root,
            'media_url': self.media_url,
        }, context_instance=Context(request, current_app=self.name))

    # delete signals
    filebrowser_pre_delete = Signal(providing_args=["path", "name"])
    filebrowser_post_delete = Signal(providing_args=["path", "name"])

    def delete(self, request):
        """
        Delete existing File/Directory.
        When trying to delete a Directory, the Directory has to be empty.
        """
        query = request.GET
        abs_path = u'%s' % os.path.join(self.media_root, query.get('dir', ''))
        fileobject = FileObject(os.path.join(abs_path, query.get('filename', '')), media_root=self.media_root, media_url=self.media_url)
        
        if request.GET:
            try:
                self.filebrowser_pre_delete.send(sender=request, path=fileobject.path, name=fileobject.filename)
                fileobject.delete_versions()
                fileobject.delete()
                self.filebrowser_post_delete.send(sender=request, path=fileobject.path, name=fileobject.filename)
                messages.add_message(request, messages.SUCCESS, _('Successfully deleted %s') % fileobject.filename)
            except OSError, (errno, strerror):
                # TODO: define error-message
                pass
        redirect_url = reverse("filebrowser:fb_browse", current_app=self.name) + query_helper(query, "", "filename,filetype")
        return HttpResponseRedirect(redirect_url)

    # rename signals
    filebrowser_pre_rename = Signal(providing_args=["path", "name", "new_name"])
    filebrowser_post_rename = Signal(providing_args=["path", "name", "new_name"])

    filebrowser_actions_pre_apply = Signal(providing_args=['action_name', 'fileobjects',])
    filebrowser_actions_post_apply = Signal(providing_args=['action_name', 'filebjects', 'result'])

    def detail(self, request):
        """
        Show detail page for a file.
        
        Rename existing File/Directory (deletes existing Image Versions/Thumbnails).
        """
        from filebrowser.forms import ChangeForm
        query = request.GET
        abs_path = u'%s' % os.path.join(self.media_root, query.get('dir', ''))
        fileobject = FileObject(os.path.join(abs_path, query.get('filename', '')), media_root=self.media_root, media_url=self.media_url)
        
        if request.method == 'POST':
            form = ChangeForm(request.POST, path=abs_path, fileobject=fileobject, site=self)
            if form.is_valid():
                new_name = form.cleaned_data['name']
                action_name = form.cleaned_data['custom_action']
                try:
                    action_response = None
                    if action_name:
                        action = self.get_action(action_name)
                        # Pre-action signal
                        self.filebrowser_actions_pre_apply.send(sender=request, action_name=action_name, fileobject=[fileobject])
                        # Call the action to action
                        action_response = action(request=request, fileobjects=[fileobject])
                        # Post-action signal
                        self.filebrowser_actions_post_apply.send(sender=request, action_name=action_name, fileobject=[fileobject], result=action_response)
                    if new_name != fileobject.filename:
                        self.filebrowser_pre_rename.send(sender=request, path=fileobject.path, name=fileobject.filename, new_name=new_name)
                        fileobject.delete_versions()
                        os.rename(fileobject.path, os.path.join(fileobject.head, new_name))
                        self.filebrowser_post_rename.send(sender=request, path=fileobject.path, name=fileobject.filename, new_name=new_name)
                        messages.add_message(request, messages.SUCCESS, _('Renaming was successful.'))
                    if isinstance(action_response, HttpResponse):
                        return action_response
                    if "_continue" in request.POST:
                        redirect_url = reverse("filebrowser:fb_detail", current_app=self.name) + query_helper(query, "filename="+new_name, "filename")
                    else:
                        redirect_url = reverse("filebrowser:fb_browse", current_app=self.name) + query_helper(query, "", "filename")
                    return HttpResponseRedirect(redirect_url)
                except OSError, (errno, strerror):
                    form.errors['name'] = forms.util.ErrorList([_('Error.')])
        else:
            form = ChangeForm(initial={"name": fileobject.filename}, path=abs_path, fileobject=fileobject, site=self)
        
        return render_to_response('filebrowser/detail.html', {
            'form': form,
            'fileobject': fileobject,
            'query': query,
            'title': u'%s' % fileobject.filename,
            'settings_var': get_settings_var(media_root=self.media_root, media_url=self.media_url),
            'breadcrumbs': get_breadcrumbs(query, query.get('dir', '')),
            'breadcrumbs_title': u'%s' % fileobject.filename,
            'media_root': self.media_root,
            'media_url': self.media_url,
        }, context_instance=Context(request, current_app=self.name))

    def version(self, request):
        """
        Version detail.
        """
        query = request.GET
        abs_path = u'%s' % os.path.join(self.media_root, query.get('dir', ''))
        fileobject = FileObject(os.path.join(abs_path, query.get('filename', '')), media_root=self.media_root, media_url=self.media_url)
        
        return render_to_response('filebrowser/version.html', {
            'fileobject': fileobject,
            'query': query,
            'settings_var': get_settings_var(media_root=self.media_root, media_url=self.media_url),
            'media_root': self.media_root,
            'media_url': self.media_url,
        }, context_instance=Context(request, current_app=self.name))

    # upload signals
    filebrowser_pre_upload = Signal(providing_args=["path", "file"])
    filebrowser_post_upload = Signal(providing_args=["path", "file"])

    def _upload_file(self, request):
        """
        Upload file to the server.
        """
        from django.core.files.move import file_move_safe

        if request.method == "POST":
            if request.is_ajax(): # Advanced (AJAX) submission
                folder = request.GET.get('folder')
                filedata = ContentFile(request.raw_post_data)
                try:
                    filedata.name = convert_filename(request.GET['qqfile'])
                except KeyError:
                    return HttpResponseBadRequest('Invalid request! No filename given.')
            else: # Basic (iframe) submission
                folder = request.POST.get('folder')
                if len(request.FILES) == 1:
                    filedata = request.FILES.values()[0]
                else:
                    raise Http404('Invalid request! Multiple files included.')
                filedata.name = convert_filename(upload.name)

            fb_uploadurl_re = re.compile(r'^.*(%s)' % reverse("filebrowser:fb_upload", current_app=self.name))
            folder = fb_uploadurl_re.sub('', folder)
            abs_path = os.path.join(self.media_root, folder)
            self.filebrowser_pre_upload.send(sender=request, path=request.POST.get('folder'), file=filedata)
            uploadedfile = handle_file_upload(abs_path, filedata, media_root=self.media_root)
            # if file already exists
            if os.path.isfile(smart_unicode(os.path.join(self.media_root, folder, filedata.name))):
                old_file = smart_unicode(os.path.join(abs_path, filedata.name))
                new_file = smart_unicode(os.path.join(abs_path, uploadedfile))
                file_move_safe(new_file, old_file, allow_overwrite=True)
            self.filebrowser_post_upload.send(sender=request, path=request.POST.get('folder'), file=FileObject(smart_unicode(os.path.join(folder, filedata.name)), media_root=self.media_root, media_url=self.media_url))
            # let Ajax Upload know whether we saved it or not
            ret_json = {'success': True, 'filename': filedata.name}
            return HttpResponse(json.dumps(ret_json))

# Default FileBrowser site
site = FileBrowserSite(name='filebrowser')

# Default actions
from actions import *
site.add_action(flip_horizontal)
site.add_action(flip_vertical)
site.add_action(rotate_90_clockwise)
site.add_action(rotate_90_counterclockwise)
site.add_action(rotate_180)
