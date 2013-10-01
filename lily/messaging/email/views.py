import datetime
import email
from django.template.defaultfilters import truncatechars
import os
import traceback
import urllib
import logging
from email import Encoders
from email.MIMEBase import MIMEBase

from bs4 import BeautifulSoup
from dateutil.tz import tzutc
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.formtools.wizard.views import SessionWizardView
from django.core.files.storage import default_storage
from django.core.mail import EmailMultiAlternatives
from django.core.urlresolvers import reverse
from django.core.servers.basehttp import FileWrapper
from django.db.models import Q
from django.http import HttpResponse, Http404
from django.shortcuts import redirect, get_object_or_404
from django.template.context import RequestContext
from django.template.loader import render_to_string
from django.utils import simplejson
from django.utils.html import escapejs
from django.utils.translation import ugettext as _
from django.views.generic.base import View, TemplateView
from django.views.generic.edit import CreateView, FormView, UpdateView
from django.views.generic.list import ListView
from imapclient.imapclient import DRAFT
from python_imap.folder import DRAFTS, INBOX, SENT, TRASH, SPAM, ALLMAIL, IMPORTANT, STARRED
from python_imap.logger import logger as imap_logger
from python_imap.server import IMAP
from python_imap.utils import convert_html_to_text, parse_search_keys

from lily.contacts.models import Contact
from lily.messaging.email.forms import CreateUpdateEmailTemplateForm, \
    EmailTemplateFileForm, ComposeEmailForm, EmailConfigurationStep1Form, \
    EmailConfigurationStep2Form, EmailConfigurationStep3Form, EmailShareForm
from lily.messaging.email.models import EmailAttachment, EmailMessage, EmailAccount, EmailTemplate, EmailProvider
from lily.messaging.email.tasks import save_email_messages, mark_messages, delete_messages, synchronize_folder, move_messages
from lily.messaging.email.utils import get_email_parameter_choices, TemplateParser, get_attachment_filename_from_url, get_remote_messages, smtp_connect, EmailMultiRelated
from lily.tenant.middleware import get_current_user
from lily.users.models import CustomUser
from lily.utils.functions import is_ajax
from lily.utils.models import EmailAddress
from lily.utils.templatetags.messages import tag_mapping
from lily.utils.views import AttachmentFormSetViewMixin, DeleteBackAddSaveFormViewMixin, FilteredListMixin, SortedListMixin


log = logging.getLogger('django.request')


class EditEmailAccountView(TemplateView):
    """
    Edit an existing e-mail account.
    """
    template_name = 'messaging/email/account_create.html'


class DetailEmailAccountView(TemplateView):
    """
    Show the details of an existing e-mail account.
    """
    template_name = 'messaging/email/account_create.html'


class ListEmailTemplateView(SortedListMixin, FilteredListMixin, ListView):
    template_name = 'messaging/email/email_template_list.html'
    model = EmailTemplate
    sortable = [1, 2]
    default_order_by = 2

    def get_context_data(self, **kwargs):
        """
        Overloading super().get_context_data to provide the list item template.
        """
        kwargs = super(ListEmailTemplateView, self).get_context_data(**kwargs)

        kwargs.update({
            'list_item_template': 'messaging/email/email_template_list_item.html',
        })

        return kwargs


class AddEmailTemplateView(DeleteBackAddSaveFormViewMixin, CreateView):
    """
    Create a new e-mail template that can be used for sending emails.
    """
    template_name = 'messaging/email/template_create_or_update.html'
    model = EmailTemplate
    form_class = CreateUpdateEmailTemplateForm

    def get_context_data(self, **kwargs):
        """

        :param kwargs: keyword arguments.
        :return: context data used to render the template.
        """
        context = super(AddEmailTemplateView, self).get_context_data(**kwargs)
        context.update({
            'parameter_choices': simplejson.dumps(get_email_parameter_choices()),
        })
        return context

    def get_success_url(self):
        """
        Redirect to the inbox view.
        """
        messages.success(self.request, _('Template saved successfully.'))

        return reverse('messaging_email_inbox')


class EditEmailTemplateView(DeleteBackAddSaveFormViewMixin, UpdateView):
    """
    Parse an uploaded template for variables and return a generated form/
    """
    template_name = 'messaging/email/template_create_or_update.html'
    model = EmailTemplate
    form_class = CreateUpdateEmailTemplateForm

    def get_context_data(self, **kwargs):
        context = super(EditEmailTemplateView, self).get_context_data(**kwargs)
        context.update({
            'parameter_choices': simplejson.dumps(get_email_parameter_choices()),
        })
        return context

    def get_success_url(self):
        """
        Redirect to the edit view, so the default values of parameters can be filled in.
        """
        messages.success(self.request, _('Template edited successfully.'))

        return reverse('messaging_email_inbox')

    def get_form_kwargs(self):
        """
        Get the keyword arguments that will be used to initiate the form.

        :return: A dict of keyword arguments.
        """
        kwargs = super(EditEmailTemplateView, self).get_form_kwargs()
        kwargs.update({
            'draft_id': self.object.pk,
            'message_type': 'template',
        })
        return kwargs


class ParseEmailTemplateView(FormView):
    """
    Parse an uploaded template for variables and return a generated form
    """
    template_name = 'messaging/email/template_create_or_update_base_form.html'
    form_class = EmailTemplateFileForm

    def form_valid(self, form):
        """
        Return parsed form with rendered parameter fields
        """
        # we return content of the file here because this easily enables us to do more sophisticated parsing in the future.
        form.cleaned_data.update({
            'valid': True,
        })

        return HttpResponse(simplejson.dumps(form.cleaned_data), mimetype="application/json")

    def form_invalid(self, form):
        return HttpResponse(simplejson.dumps({
            'valid': False,
            'errors': form.errors,
        }), mimetype="application/json")


class EmailFolderView(ListView):
    """
    Show a list of e-mail messages in a certain folder.
    """
    template_name = 'messaging/email/model_list.html'
    paginate_by = 10
    folder_name = None
    folder_identifier = None

    def get(self, request, *args, **kwargs):
        # Determine which accounts to show messages from
        if kwargs.get('account_id'):
            self.email_accounts = request.user.get_messages_accounts(EmailAccount, pk__in=[kwargs.get('account_id')])
        else:
            self.email_accounts = request.user.get_messages_accounts(EmailAccount)

        # Deteremine which folder to show messages from
        if kwargs.get('folder') and not any([self.folder_name, self.folder_identifier]):
            self.folder_name = self.folder_identifier = urllib.unquote_plus(kwargs.get('folder'))

        return super(EmailFolderView, self).get(request, *args, **kwargs)

    def get_queryset(self, tried_remote=False):
        """
        Return empty queryset or return it filtered based on folder_name and/or folder_identifier.
        """
        qs = EmailMessage.objects.none()
        if self.folder_name is not None and self.folder_identifier is not None:
            qs = EmailMessage.objects.filter(Q(folder_identifier=self.folder_identifier) | Q(folder_name=self.folder_name))
        elif self.folder_name is not None:
            qs = EmailMessage.objects.filter(folder_name=self.folder_name)
        elif self.folder_identifier is not None:
            qs = EmailMessage.objects.filter(folder_identifier=self.folder_identifier)
        qs = qs.filter(account__in=self.email_accounts).extra(select={
            'num_attachments': 'SELECT COUNT(*) FROM email_emailattachment WHERE email_emailattachment.message_id = email_emailmessage.message_ptr_id AND inline=False'
        }).order_by('-sent_date')

        # Try remote fetch when no results have been found locally
        if not tried_remote and len(qs) == 0:
            for account in self.email_accounts:
                get_remote_messages(account, self.folder_identifier or self.folder_name)
            qs = self.get_queryset(tried_remote=True)

        return qs

    def get_context_data(self, **kwargs):
        """
        Overloading super().get_context_data to provide the list item template.
        """
        kwargs = super(EmailFolderView, self).get_context_data(**kwargs)
        kwargs.update({
            'list_item_template': 'messaging/email/model_list_item.html',
            'list_title': ', '.join([email_account.email.email_address for email_account in self.email_accounts]),
        })

        from collections import OrderedDict
        folders = OrderedDict()

        folders['Postvak In'] = 'Postvak IN'
        if self.folder_identifier not in [SENT, TRASH]:
            # Find folders for visible accounts
            for account in self.email_accounts:
                def get_folders_from_tree(tree):
                    for name, folder in tree.items():
                        if folder.get('is_parent'):
                            if '\\Noselect' not in folder.get('flags'):
                                folders[name] = folder.get('full_name')

                            sub_folders = folder.get('children')
                            if len(sub_folders):
                                get_folders_from_tree(sub_folders)
                        elif name in tree:
                            if '\\Noselect' not in folder.get('flags'):
                                intersect = set([INBOX, SENT, DRAFTS, TRASH, SPAM, ALLMAIL, IMPORTANT, STARRED]).intersection(set(folder.get('flags', [])))
                                if len(intersect) > 0:
                                    # If folder already exists, remove it since it probably wasn't added with *intersect* as the key
                                    if folder.get('full_name') in folders.values():
                                        del folders[folders.keys()[folders.values().index(folder.get('full_name'))]]
                                    folders[intersect.pop()] = folder.get('full_name')
                                elif not folder.get('full_name') in folders.values():
                                    # Don't add to avoid duplicates
                                    folders[name] = folder.get('full_name')

                get_folders_from_tree(account.folders)

        # Sort dictionary by value (folder name)
        folders = OrderedDict(sorted(folders.items(), key=lambda t: t[1]))

        # Find active folder
        active_move_to_folder = ''
        if self.folder_identifier in [INBOX, SENT, DRAFTS, TRASH, SPAM]:
            for account in self.email_accounts:
                for name, folder in account.folders.items():
                    if len(set([INBOX, SENT, DRAFTS, TRASH, SPAM]).intersection(set(folder.get('flags', [])))) > 0:
                        active_move_to_folder = folder.get('full_name')
                        break
        else:
            active_move_to_folder = self.folder_identifier

        # Also pass search parameters, if any
        kwargs.update({
            'selected_email_account_id': self.kwargs.get('account_id', ''),
            'selected_email_folder': urllib.quote_plus(self.kwargs.get('folder', self.folder_name or self.folder_identifier)),
            'email_search_key': self.kwargs.get('search_key', ''),
            'move_to_folders': folders,
            'active_move_to_folder': active_move_to_folder,
        })

        return kwargs


class EmailInboxView(EmailFolderView):
    """
    Show INBOX folder for all accessible messages accounts.
    """
    folder_identifier = INBOX


class EmailDraftsView(EmailFolderView):
    """
    Show DRAFTS folder for all accessible messages accounts.
    """
    folder_identifier = DRAFTS


class EmailSentView(EmailFolderView):
    """
    Show SENT folder for all accessible messages accounts.
    """
    folder_identifier = SENT


class EmailTrashView(EmailFolderView):
    """
    Show TRASH folder for all accessible messages accounts.
    """
    folder_identifier = TRASH


class EmailSpamView(EmailFolderView):
    """
    Show SPAM folder for all accessible messages accounts.
    """
    folder_identifier = SPAM


class BaseJSONViewMixin(View):
    """
    Show most attributes of an EmailMessage in JSON format.
    """
    http_method_names = ['get']
    template_name = 'messaging/email/email_heading.html'
    mark_as_read = True
    use_rich_body = True

    def unix_time(self, dt):
        """
        Get epoch time in milliseconds

        :param dt: datetime object
        :return: epoch ms time
        """
        epoch = datetime.datetime.fromtimestamp(0, tz=dt.tzinfo)
        delta = dt - epoch
        return delta.total_seconds()

    def unix_time_millis(self, dt):
        """
        Get epoch time in milliseconds

        :param dt: datetime object
        :return: epoch ms time
        """
        return self.unix_time(dt) * 1000.0

    def get(self, request, *args, **kwargs):
        """
        Retrieve the email for the requested uid from the database or directly via IMAP.
        """
        # Find accounts
        self.email_accounts = request.user.get_messages_accounts(EmailAccount)
        server = None
        pk = kwargs.get('pk')
        try:
            instance = EmailMessage.objects.get(pk=pk)
            imap_logger.info('Retrieving message for e-mail account: %s' % instance.account.email.email_address)
            # See if the user has access to this message
            if instance.account not in self.email_accounts:
                raise Http404()

            if (instance.body_html is None or len(instance.body_html.strip()) == 0) and (
                        instance.body_text is None or len(instance.body_text.strip()) == 0):
                server, instance = self.get_message_from_imap(instance, pk)

            if self.mark_as_read:
                instance.is_seen = True

            instance.save()

            message = {
                'id': instance.id,
                'sent_date': self.unix_time_millis(instance.sent_date),
                'flags': instance.flags,
                'uid': instance.uid,
                'flat_body': self.get_flat_body(instance),
                'subject': instance.subject.encode('utf-8') or u'<%s>' % _('No subject'),
                'size': instance.size,
                'is_private': instance.is_private,
                'is_read': instance.is_seen,
                'is_plain': instance.is_plain,
                'folder_name': instance.folder_name,
            }

            instance, message, attachments = self.get_attachments(instance, message)

            imap_logger.debug(instance)
            imap_logger.debug(message)
            imap_logger.debug(attachments)

            if self.use_rich_body:
            # Replace body with a more richer version of an e-mail view
                message['body'] = render_to_string(self.template_name, {'object': instance, 'attachments': attachments})

            return HttpResponse(simplejson.dumps(message), mimetype='application/json; charset=utf-8')
        except EmailMessage.DoesNotExist:
            raise Http404()
        finally:
            if server:
                server.logout()

    def get_flat_body(self, instance):
        """
        Create the flat body for in the message

        :param instance: The email message instance
        :return: a flat body string
        """
        return truncatechars(instance.textify().lstrip('&nbsp;\n\r\n '), 200)

    def get_message_from_imap(self, instance, pk):
        """
        Retrieve an e-mail via IMAP

        :param instance: the instance of a message
        :param pk: the primary key of a message
        :return: server used for connecting and the new updated instance
        """
        imap_logger.info('Connecting with IMAP')

        host = instance.account.provider.imap_host
        port = instance.account.provider.imap_port
        ssl = instance.account.provider.imap_ssl
        server = IMAP(host, port, ssl)
        server.login(instance.account.username, instance.account.password)

        imap_logger.info('Searching IMAP for %s in %s' % (instance.uid, instance.folder_name))

        message = server.get_message(instance.uid, ['BODY[]', 'FLAGS', 'RFC822.SIZE', 'INTERNALDATE'],
                                     server.get_folder(instance.folder_name), readonly=False)
        if message is not None:
            imap_logger.info('Message retrieved, saving in database')
            save_email_messages([message], instance.account, message.folder)

        instance = EmailMessage.objects.get(pk=pk)

        return server, instance

    def get_attachments(self, instance, message):
        """
        Get the attachments for the message

        :param instance: the instance of which we want the attachments
        :param message: the message to which we append the attachments
        :return: the instance and the message
        """
        # By default we don't get attachments
        attachments = None
        return instance, message, attachments


class EmailMessageJSONView(BaseJSONViewMixin):
    """
    Show most attributes of an EmailMessage in JSON format.
    """

    def get_attachments(self, instance, message):
        """
        Get the attachments for the message

        :param instance: the instance of which we want the attachments
        :param message: the message to which we append the attachments
        :return: the instance, message and attachments
        """
        attachments = instance.attachments.filter(inline=False)
        if len(attachments):
            for attachment in attachments:
                attachment.attachment.name = get_attachment_filename_from_url(attachment.attachment.name)

        return instance, message, attachments


class HistoryListEmailMessageJSONView(BaseJSONViewMixin):
    """
    Show most attributes of an EmailMessage in JSON format.
    """
    mark_as_read = False
    use_rich_body = False

    def get_flat_body(self, instance):
        """
        Create the flat body for in the message

        :param instance: The email message instance
        :return: a flat body string
        """
        return instance.textify().strip('&nbsp;\n\r\n ').replace('\n', '<br />')


class EmailMessageHTMLView(View):
    """
    Return the HTML for single e-mail message.
    """
    http_method_names = ['get']
    template_name = 'messaging/email/email_body.html'

    def get(self, request, *args, **kwargs):
        try:
            instance = EmailMessage.objects.get(id=kwargs.get('pk'))

            if instance.body_html:
                body = render_to_string(self.template_name, {'is_plain': False, 'body': instance.body_html.encode('utf-8')})
            else:
                body = render_to_string(self.template_name, {'is_plain': True, 'body': instance.body_text.encode('utf-8')})

            return HttpResponse(body, mimetype='text/html; charset=utf-8')
        except EmailMessage.DoesNotExist:
            raise Http404()


class MessageUpdateView(View):
    """
    Handle various AJAX calls for n messages.
    """
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        try:
            message_ids = request.POST.getlist('ids[]')
            if not isinstance(message_ids, list):
                message_ids = [message_ids]
            if len(message_ids) > 0:
                self.handle_message_update(message_ids)
        except:
            raise Http404()

        # Return response
        return HttpResponse(simplejson.dumps({}), mimetype='application/json')

    def handle_message_update(self, message_ids):
        raise NotImplementedError("Implement by subclassing MessageUpdateView")


class MarkReadAjaxView(MessageUpdateView):
    """
    Mark messages as read.
    """
    def handle_message_update(self, message_ids):
        mark_messages.delay(message_ids, read=True)


class MarkUnreadAjaxView(MessageUpdateView):
    """
    Mark messages as unread.
    """
    def handle_message_update(self, message_ids):
        mark_messages.delay(message_ids, read=False)


class MoveTrashAjaxView(MessageUpdateView):
    """
    Move messages to trash.
    """
    def handle_message_update(self, message_ids):
        delete_messages.delay(message_ids)


class MoveMessagesView(MessageUpdateView):
    """
    Move messages to selected folder.
    """
    def post(self, request, *args, **kwargs):
        if not request.META.get('HTTP_REFERER'):
            raise Http404()

        try:
            message_ids = request.POST.get('ids')
            if not isinstance(message_ids, list):
                message_ids = message_ids.split(',')

            if len(message_ids) > 0:
                if request.POST.get('move-to-folder-select', False):
                    move_messages(message_ids, request.POST.get('move-to-folder-select'), request)
        except:
            raise Http404()

        # Simply return back to the page the request originated from
        return redirect(request.META.get('HTTP_REFERER'))


class ForceFolderSyncView(View):
    """
    Synchronize a folder with minimal headers and redirect to the first page.
    """
    folder_name = None
    folder_identifier = None

    def get(self, request, *args, **kwargs):
        # Determine which accounts to show messages from
        if kwargs.get('account_id'):
            email_accounts = request.user.get_messages_accounts(EmailAccount, pk__in=[kwargs.get('account_id')])
        else:
            email_accounts = request.user.get_messages_accounts(EmailAccount)

        # Deteremine which folder to show messages from
        folder_name = urllib.unquote_plus(kwargs.get('folder'))
        identifier = None

        # Synchronize self.folder for self.email_accounts
        for account in email_accounts:
            server = None
            try:
                host = account.provider.imap_host
                port = account.provider.imap_port
                ssl = account.provider.imap_ssl
                server = IMAP(host, port, ssl)
                server.login(account.username,  account.password)

                if '\\%s' % folder_name in [x.lower() for x in [INBOX, SENT, DRAFTS, TRASH, SPAM]]:
                    for folder_identifier in [INBOX, SENT, DRAFTS, TRASH, SPAM]:
                        if  '\\%s' % folder_name == folder_identifier.lower():
                            identifier = folder_identifier

                if identifier is not None:
                    folder = server.get_folder(identifier)
                else:
                    folder = server.get_folder(folder_name)

                modifiers = ['BODY.PEEK[HEADER.FIELDS (Reply-To Subject Content-Type To Cc Bcc Delivered-To From Message-ID Sender In-Reply-To Received Date)]', 'FLAGS', 'RFC822.SIZE', 'INTERNALDATE']
                synchronize_folder(account, server, folder, modifiers_old=modifiers, modifiers_new=modifiers)

            finally:
                if server:
                    server.logout()

        if identifier in [INBOX, SENT, DRAFTS, TRASH, SPAM]:
            if kwargs.get('account_id'):
                reverse_url_name = {
                    INBOX: 'messaging_email_account_inbox',
                    SENT: 'messaging_email_account_sent',
                    DRAFTS: 'messaging_email_account_drafts',
                    TRASH: 'messaging_email_account_trash',
                    SPAM: 'messaging_email_account_spam',
                }.get(identifier)
                folder_url = reverse(reverse_url_name, kwargs={'account_id': kwargs.get('account_id')})
            else:
                reverse_url_name = {
                    INBOX: 'messaging_email_inbox',
                    SENT: 'messaging_email_sent',
                    DRAFTS: 'messaging_email_drafts',
                    TRASH: 'messaging_email_trash',
                    SPAM: 'messaging_email_spam',
                }.get(identifier)
                folder_url = reverse(reverse_url_name)
        else:
            folder_url = reverse('messaging_email_account_folder', kwargs={
                'account_id': kwargs.get('account_id'),
                'folder': urllib.quote_plus(folder_name)
            })

        return redirect(folder_url)


class EmailComposeView(AttachmentFormSetViewMixin, FormView):
    """
    View where you compose e-mail messages and either send or save them.
    """
    template_name = 'messaging/email/email_compose.html'
    form_class = ComposeEmailForm
    message_object_query_args = ()
    remove_old_message = True

    def dispatch(self, request, *args, **kwargs):
        """
        Make sure the right handler is called for the type of request.

        :param request: The browser request tot this view.
        :param args: Arguments passed to this view.
        :param kwargs: Keyword arguments passed to this view.
        :return: A HttpResponse object.
        """
        self.message_id = kwargs.get('pk')
        if self.message_id:
            self.instance = get_object_or_404(EmailMessage, self.message_object_query_args, pk=self.message_id)
        return super(EmailComposeView, self).dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        """
        Get the keyword arguments that will be used to initiate the form.

        :return: A dict of keyword arguments.
        """
        kwargs = super(EmailComposeView, self).get_form_kwargs()
        kwargs['message_type'] = 'new'

        if hasattr(self, 'instance'):
            kwargs.update({
                'draft_id': self.instance.pk,
                'initial': {
                    'send_from': self.instance.from_email,
                    'subject': self.instance.subject,
                    'send_to_normal': self.instance.to_combined,
                    'send_to_cc': self.instance.to_cc_combined,
                    'send_to_bcc': self.instance.to_bcc_combined,
                    'body_text': self.instance.body_text,
                },
            })
        return kwargs

    def form_valid(self, form):
        """
        Handle the form data according to the type of submit.

        :param form: The form that was validated successfully.
        :return: A HttpResponse object.
        """
        unsaved_form = form.save(commit=False)
        server = None

        try:
            account = unsaved_form.send_from

            host = account.provider.imap_host
            port = account.provider.imap_port
            ssl = account.provider.imap_ssl
            server = IMAP(host, port, ssl)
            server.login(account.username,  account.password)

            # Create python email message object
            if 'submit-save' in self.request.POST or 'submit-send' in self.request.POST:
                # Search for inline images, and replace the src attribute with Content-IDs
                soup = BeautifulSoup(unsaved_form.body_html, 'permissive')

                inline_application_url = '/messaging/email/attachment/'
                inline_application_images = soup.findAll('img', {'src': lambda src: src and src.startswith(inline_application_url)})

                # Mapping attachment.pk : image element
                mapped_attachments = {}

                # Parse attachment pks from image paths
                for image in inline_application_images:
                    pk_and_path = image.get('src')[len(inline_application_url):].rstrip('/')
                    parts = pk_and_path.split('/')
                    pk = int(parts[0])
                    mapped_attachments[pk] = image

                kwargs = dict(
                    subject=unsaved_form.subject,
                    from_email=account.email.email_address,
                    to=[unsaved_form.send_to_normal] if len(unsaved_form.send_to_normal) else None,
                    bcc=[unsaved_form.send_to_bcc] if len(unsaved_form.send_to_bcc) else None,
                    connection=None,
                    attachments=None,
                    headers=self.get_email_headers(),
                    alternatives=None,
                    cc=[unsaved_form.send_to_cc] if len(unsaved_form.send_to_cc) else None,
                )

                # When sending the e-mail, potentially convert the HTML to plain/text, but don't do this for drafts
                if 'submit-send' in self.request.POST:
                    kwargs.update({
                        'body': unsaved_form.body_text or convert_html_to_text(unsaved_form.body_html),  # TODO replace inline images with filenames or alt/title attributes ? (should be attachments when viewing text/plain
                    })
                else:
                    kwargs.update({
                        'body': unsaved_form.body_text
                    })

                # Use EmailMultiAlternatives for text/(plain|html) e-mails
                if len(mapped_attachments.keys()) == 0:
                    email_message = EmailMultiAlternatives(**kwargs)
                    # Attach html as alternative to *body*
                    email_message.attach_alternative(unsaved_form.body_html, 'text/html')
                else:
                    # Use multipart/related when using inline images
                    email_message = EmailMultiRelated(**kwargs)

                    # Put imagedata for attachments in *email_message*
                    attachments = EmailAttachment.objects.filter(pk__in=mapped_attachments.keys())
                    for attachment in attachments:
                        storage_file = default_storage._open(attachment.attachment.name)
                        filename = get_attachment_filename_from_url(attachment.attachment.name)

                        # Add as inline attachment
                        storage_file.open()
                        content = storage_file.read()
                        storage_file.close()
                        email_message.attach_related(filename, content, storage_file.key.content_type)

                        # Update attribute src for inline image
                        inline_image = mapped_attachments[attachment.pk]
                        inline_image['src'] = 'cid:%s' % filename

                    # Use new HTML
                    unsaved_form.body_html = soup.encode_contents()
                    email_message.attach_alternative(unsaved_form.body_html, 'text/html')

                success = True
                if 'submit-save' in self.request.POST:  # Save draft
                    success = self.save_message(account, server, email_message)
                elif 'submit-send' in self.request.POST:  # Send draft
                    if self.message_id:
                        email_message = self.attach_stored_files(email_message, self.message_id)

                    success = self.send_message(account, server, email_message)

                # Remove (old) drafts in every case
                if hasattr(self, 'instance') and success and self.remove_old_message:
                    self.remove_draft(server)

            if 'submit-discard' in self.request.POST and hasattr(self, 'instance') and self.remove_old_message:
                self.remove_draft(server)
        except Exception, e:
            log.error(traceback.format_exc(e))
        finally:
            if server:
                server.logout()

        return super(EmailComposeView, self).form_valid(form)

    def attach_request_files(self, email_message):
        """
        Attach files from request.FILES to *email_message* as separte mime parts.
        """
        attachments = self.request.FILES
        if len(attachments) > 0:
            for key, attachment in attachments.items():
                filetype = attachment.content_type.split('/')
                part = MIMEBase(filetype[0], filetype[1])
                part.set_payload(attachment.read())
                Encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(attachment.name))

                email_message.attach(part)

        return email_message

    def attach_stored_files(self, email_message, pk):
        """
        Attach EmailAttachments to *email_message* as separte mime parts.
        """
        attachments = EmailAttachment.objects.filter(inline=False, message_id=pk).all()
        if len(attachments) > 0:
            for attachment in attachments:
                storage_file = default_storage._open(attachment.attachment.name)
                filename = get_attachment_filename_from_url(attachment.attachment.name)

                storage_file.open()
                content = storage_file.read()
                storage_file.close()

                filetype = storage_file.key.content_type.split('/')
                part = MIMEBase(filetype[0], filetype[1])
                part.set_payload(content)
                Encoders.encode_base64(part)
                part.add_header('Content-Disposition', 'attachment; filename="%s"' % os.path.basename(filename))

                email_message.attach(part)

        return email_message

    def save_message(self, account, server, email_message):
        """
        Save the message as a draft to the database and to the server via IMAP.
        """
        # Check for attachments
        email_message = self.attach_request_files(email_message)
        if hasattr(self, 'instance') and self.instance.pk:
            email_message = self.attach_stored_files(email_message, self.instance.pk)

        message_string = unicode(email_message.message().as_string(unixfrom=False))

        try:
            # Save *email_message* as draft
            folder = server.get_folder(DRAFTS)

            # Save draft remotely
            response = server.client.append(folder.name_on_server, message_string, flags=[DRAFT], msg_time=datetime.datetime.now(tzutc()))

            # Extract uid from response
            command, seq, uid, status = [part.strip('[]()') for part in response.split(' ')]
            uid = int(uid)

            # Sync this specific message
            message = server.get_message(
                uid,
                modifiers=['BODY.PEEK[]', 'FLAGS', 'RFC822.SIZE'],
                folder=folder
            )
            save_email_messages(
                [message],
                account,
                folder,
                new_messages=True
            )
            self.new_draft = EmailMessage.objects.get(
                account=account,
                uid=uid,
                folder_name=folder.name_on_server
            )
        except Exception, e:
            log.error(traceback.format_exc(e))
            return False

        return True

    def send_message(self, account, active_server, email_message):
        """
        Send the message via SMTP and save the sent message to the database.

        :param server: The server on which the message needs to be sent.
        :param email_message: The message that needs to be sent.
        :return: A Boolean indicating whether the save was successful.
        """
        # Check for attachments
        email_message = self.attach_request_files(email_message)

        try:
            # Send initial message
            connection = smtp_connect(account, fail_silently=False)
            connection.send_messages([email_message])

            # Send extra for BCC recipients if any
            if email_message.bcc:
                recipients = email_message.bcc

                # Send separate messages
                for recipient in recipients:
                    email_message.bcc = []
                    email_message.to = [recipient]
                    connection.send_messages([email_message])

            # Synchronize only new messages from folder *SENT*
            synchronize_folder(
                account,
                active_server,
                active_server.get_folder(SENT),
                criteria=['subject "%s"' % email_message.subject],
                new_only=True
            )
        except Exception, e:
            log.error(traceback.format_exc(e))
            return False

        return True

    def remove_draft(self, server):
        """
        Remove old version of the message from the server and the database.

        :param server: The server from which the old message needs to be removed.
        """
        if self.instance.uid:
            folder = server.get_folder(DRAFTS)
            is_selected, select_info = server.select_folder(folder.get_search_name(), readonly=False)
            if is_selected:
                server.client.delete_messages([self.instance.uid])
                server.client.close_folder()

        # Delete local attachments
        self.instance.attachments.all().delete()
        self.instance.delete()

    def get_email_headers(self):
        """
        This function is not implemented. For custom headers overwrite this function.
        """
        pass

    def get_context_data(self, **kwargs):
        """
        Get context data that is used for the rendering of the template.

        :param kwargs: Keyword arguments.
        :return: A dict containing the context data.
        """
        context = super(EmailComposeView, self).get_context_data(**kwargs)

        # Query for all contacts which have e-mail addresses
        contacts_addresses_qs = Contact.objects.filter(
            email_addresses__in=EmailAddress.objects.all()
        ).prefetch_related('email_addresses')

        known_contact_addresses = []
        for contact in contacts_addresses_qs:
            for email_address in contact.email_addresses.all():
                contact_address = u'"%s" <%s>' % (contact.full_name(), email_address.email_address)
                known_contact_addresses.append(contact_address)

        templates = EmailTemplate.objects.all()
        template_list = {}
        for template in templates:
            template_list.update({
                template.pk: {
                    'subject': template.subject,
                    'html_part': TemplateParser(template.body_html).render(self.request),
                    'text_part': TemplateParser(template.body_text).render(self.request),
                }
            })

        context.update({
            'known_contact_addresses': simplejson.dumps(known_contact_addresses),
            'template_list': simplejson.dumps(template_list),
        })

        return context

    def get_success_url(self):
        """
        Return the appropriate success URL depending on the button pressed.

        :return: A success URL.
        """
        if 'submit-save' in self.request.POST:
            return reverse('messaging_email_compose', kwargs={'pk': self.new_draft.pk})
        elif 'submit-send' in self.request.POST:
            return reverse('messaging_email_inbox')
        elif 'submit-back' in self.request.POST or 'submit-discard' in self.request.POST:
            return reverse('messaging_email_drafts')
        else:
            return reverse('messaging_email_inbox')


class EmailCreateView(EmailComposeView):
    template_name = 'messaging/email/email_compose.html'
    form_class = ComposeEmailForm
    message_object_query_args = (Q(folder_identifier=DRAFTS.lstrip('\\')) | Q(flags__icontains='draft'))


class EmailReplyView(EmailComposeView):
    message_object_query_args = (~Q(folder_identifier=DRAFTS.lstrip('\\')) & ~Q(flags__icontains='draft'))
    remove_old_message = False

    def get_form_kwargs(self, **kwargs):
        kwargs = super(EmailReplyView, self).get_form_kwargs(**kwargs)

        if hasattr(self, 'instance'):
            kwargs['initial']['subject'] = 'Re: %s' % self.instance.subject
            kwargs['initial']['send_to_normal'] = self.instance.from_combined
            kwargs['message_type'] = 'reply'

        return kwargs

    def get_email_headers(self):
        """
        Return reply-to e-mail header.
        """
        email_headers = {}
        if hasattr(self.instance, 'send_from'):
            sender = email.utils.parseaddr(self.message.send_from)
            reply_to_name = sender[0]
            reply_to_address = sender[1]
            email_headers.update({'Reply-To': '"%s" <%s>' % (reply_to_name, reply_to_address)})
        return email_headers


class EmailForwardView(EmailReplyView):
    message_object_query_args = (~Q(folder_identifier=DRAFTS.lstrip('\\')) & ~Q(flags__icontains='draft'))
    remove_old_message = False

    def get_form_kwargs(self, **kwargs):
        """
        Get the keyword arguments that will be used to initiate the form.

        :return: A dict of keyword arguments.
        """
        kwargs = super(EmailForwardView, self).get_form_kwargs(**kwargs)

        if hasattr(self, 'instance'):
            kwargs['initial']['subject'] = 'Fwd: %s' % self.instance.subject
            kwargs['message_type'] = 'forward'

        return kwargs


class EmailBodyPreviewView(TemplateView):
    template_name = 'messaging/email/email_compose_frame.html'  # default for non-templated e-mails

    def dispatch(self, request, *args, **kwargs):
        self.object_id = kwargs.get('object_id', None)
        # self.message_id = kwargs.get('message_id', None)
        self.message_type = kwargs.get('message_type', None)
        self.template_id = kwargs.get('template_id', None)
        self.message = None

        if self.message_type == 'template' and self.object_id:
            self.template = get_object_or_404(
                EmailTemplate,
                pk=self.object_id
            )
        elif self.object_id:
            self.message = get_object_or_404(
                EmailMessage,
                pk=self.object_id
            )

            if self.template_id:
                self.template = get_object_or_404(
                    EmailTemplate,
                    pk=self.template_id
                )

        return super(EmailBodyPreviewView, self).dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs = super(EmailBodyPreviewView, self).get_context_data(**kwargs)

        if self.message_type == 'template' and self.object_id:
            body = self.template.body_html
        elif self.message_type == 'new':
            if self.message is None:
                body = u''
            else:
                body = self.message.body_html
        elif self.object_id:
            quoted_content = self.message.indented_body

            notice = None
            if self.message_type == 'forward':
                notice = _('Begin forwarded message:')
            elif self.message_type == 'reply':
                notice = _('On %s, %s wrote:' % (
                    self.message.sent_date.strftime(_('%b %e, %Y, at %H:%M')),
                    self.message.from_combined)
                )

            if notice is not None:
                quoted_content = '<div>' + notice + '</div>' + quoted_content

            if hasattr(self, 'template'):
                template = TemplateParser(self.template.body_html).render(self.request) or self.template.body_text
                body = '<div>' + template + '</div>' + '<br />' * 2 + quoted_content
            else:
                signature = u''
                body = signature + '<br />' * 2 + quoted_content
        else:
            body = u''

        kwargs.update({
            'draft': body
        })

        return kwargs


class EmailConfigurationWizardTemplate(TemplateView):
    """
    View to provide html for wizard form skeleton to configure e-mail accounts.
    """
    template_name = 'messaging/email/wizard_configuration_form.html'


class EmailConfigurationView(SessionWizardView):
    template_name = 'messaging/email/wizard_configuration_form_step.html'

    def dispatch(self, request, *args, **kwargs):
        # Verify email address exists
        self.email_address_id = kwargs.get('pk')
        try:
            self.email_address = EmailAddress.objects.get(pk=self.email_address_id)
        except EmailAddress.DoesNotExist:
            raise Http404()

        # Set up initial values per step
        self.initial_dict = {
            '0': {},
            '1': {},
            '2': {}
        }

        # Default: email as username
        self.initial_dict['0']['email'] = self.initial_dict['0']['username'] = self.email_address.email_address

        try:
            email_account = EmailAccount.objects.get(email=self.email_address)
        except EmailAccount.DoesNotExist:
            # Set from_name
            contacts = self.email_address.contact_set.all()
            if len(contacts) > 0:  # check to be safe, but should always have a contact when using this format
                contact = contacts[0]
                self.initial_dict['2']['name'] = contact.full_name()
        else:
            # Set provider data
            self.initial_dict['1']['imap_host'] = email_account.provider.imap_host
            self.initial_dict['1']['imap_port'] = email_account.provider.imap_port
            self.initial_dict['1']['imap_ssl'] = email_account.provider.imap_ssl
            self.initial_dict['1']['smtp_host'] = email_account.provider.smtp_host
            self.initial_dict['1']['smtp_port'] = email_account.provider.smtp_port
            self.initial_dict['1']['smtp_ssl'] = email_account.provider.smtp_ssl

            # Set from_name and signature
            self.initial_dict['2']['name'] = email_account.from_name
            self.initial_dict['2']['signature'] = email_account.signature

        return super(EmailConfigurationView, self).dispatch(request, *args, **kwargs)

    def get(self, *args, **kwargs):
        """
        Reset storage on first request.
        """
        self.storage.reset()
        return super(EmailConfigurationView, self).get(*args, **kwargs)

    def post(self, *args, **kwargs):
        """
        Override POST to validate the form first.
        """
        # Get the form for the current step
        form = self.get_form(data=self.request.POST.copy())

        # On first request, there's nothing to validate
        if self.storage.current_step is None:
            # Render the same form if it's not valid, continue otherwise
            if form.is_valid():
                return super(EmailConfigurationView, self).post(*args, **kwargs)
            return self.render(form)
        elif form.is_valid():
            self.storage.set_step_data(self.steps.current, self.process_step(form))
        else:
            return self.render(form)
        return super(EmailConfigurationView, self).post(*args, **kwargs)

    def get_form_kwargs(self, step=None):
        """
        Returns the keyword arguments for instantiating the form
        (or formset) on the given step.
        """
        kwargs = super(EmailConfigurationView, self).get_form_kwargs(step)

        if int(step) == 1:
            cleaned_data = self.get_cleaned_data_for_step(unicode(int(step) - 1))
            if cleaned_data is not None:
                kwargs.update({
                    'username': cleaned_data.get('username'),
                    'password': cleaned_data.get('password'),
                })

        return kwargs

    def done(self, form_list, **kwargs):
        data = {}
        for form in self.form_list.keys():
            data[form] = self.get_cleaned_data_for_step(form)

        # Save provider and emailaccount instances
        provider = EmailProvider()
        provider.imap_host = data['1']['imap_host']
        provider.imap_port = data['1']['imap_port']
        provider.imap_ssl = data['1']['imap_ssl']
        provider.smtp_host = data['1']['smtp_host']
        provider.smtp_port = data['1']['smtp_port']
        provider.smtp_ssl = data['1']['smtp_ssl']

        provider.save()

        try:
            account = EmailAccount.objects.get(email=self.email_address)
        except EmailAccount.DoesNotExist:
            account = EmailAccount()
            account.email = self.email_address

        account.account_type = 'email'
        account.from_name = data['2']['name']
        account.signature = data['2']['signature']
        account.username = data['0']['username']
        account.password = data['0']['password']
        account.provider = provider
        account.last_sync_date = datetime.datetime.now(tzutc()) - datetime.timedelta(days=1)
        account.save()

        # Link contact's user to emailaccount
        account.user_group.add(get_current_user())

        return HttpResponse(render_to_string(self.template_name, {'messaging_email_inbox': reverse('messaging_email_inbox')}, None))


class EmailShareView(FormView):
    """
    Display a form to share an email account with everybody or certain people only.
    """
    template_name = 'messaging/email/wizard_share_form.html'
    form_class = EmailShareForm

    def dispatch(self, request, *args, **kwargs):
        # Verify email address exists
        email_address_id = kwargs.get('pk')
        try:
            self.email_address = EmailAddress.objects.get(pk=email_address_id)
        except EmailAddress.DoesNotExist:
            raise Http404()

        # Verify email account exists
        try:
            self.email_account = EmailAccount.objects.get(email=self.email_address)
        except EmailAccount.DoesNotExist:
            raise Http404()

        return super(EmailShareView, self).dispatch(request, *args, **kwargs)

    def get_form_kwargs(self, **kwargs):
        original_user = None
        try:
            original_user = CustomUser.objects.get(tenant=get_current_user().tenant, contact__email_addresses__email_address=self.email_address, contact__email_addresses__is_primary=True)
        except CustomUser.DoesNotExist:
            pass

        kwargs = super(EmailShareView, self).get_form_kwargs(**kwargs)
        kwargs.update({
            'instance': self.email_account,
            'original_user': original_user
        })

        return kwargs

    def form_valid(self, form):
        """
        Handle form submission via AJAX or show custom save message.
        """
        self.object = form.save()  # copied from ModelFormMixin
        message = _('Sharing options for %s have been saved.') % self.object.email.email_address

        if is_ajax(self.request):
            # Return response
            return HttpResponse(simplejson.dumps({
                'error': False,
                'notification': [{'message': escapejs(message), 'tags': tag_mapping.get('success')}]
            }), mimetype='application/json')

        # Show save message
        messages.success(self.request, message)

        return super(EmailShareView, self).form_valid(form)

    def form_invalid(self, form):
        """
        Overloading super().form_invalid to return a different response to ajax requests.
        """
        if is_ajax(self.request):
            context = RequestContext(self.request, self.get_context_data(form=form))
            return HttpResponse(simplejson.dumps({
                'error': True,
                'html': render_to_string(self.template_name, context_instance=context)
            }), mimetype='application/json')

        return super(EmailShareView, self).form_invalid(form)


class EmailSearchView(EmailFolderView):
    """
    ListView that parses search arguments and retrieves messages
    from all the user's email accounts.
    """
    def get(self, request, *args, **kwargs):
        """
        Parse search keys and search via IMAP.
        """
        if not any([kwargs.get('account_id', None), kwargs.get('folder', None)]):
            if not any([request.GET.get('account_id', None), request.GET.get('folder', None)]):
                raise Http404()
            if request.GET.get('account_id'):
                return redirect(reverse('messaging_email_search', kwargs={'account_id': request.GET.get('account_id', None), 'folder': request.GET.get('folder', None), 'search_key': request.GET.get('search_key', None)}))
            else:
                return redirect(reverse('messaging_email_search_all', kwargs={'folder': request.GET.get('folder', None), 'search_key': request.GET.get('search_key', None)}))

        # Look in url which account id and folder the searched is performed in
        account_id = kwargs.get('account_id', None)

        # Get accounts the user has access to
        if account_id is not None:
            accounts = [int(account_id)]
            accounts_qs = request.user.get_messages_accounts(EmailAccount, pk__in=accounts)

            if len(accounts_qs) == 0:  # when provided, but no matches were found raise 404
                raise Http404()
        else:
            accounts_qs = request.user.get_messages_accounts(EmailAccount)

        # Get folder name from url or settle for ALLMAIL
        folder_name = kwargs.get('folder', None)
        if folder_name is not None:
            self.folder = urllib.unquote_plus(folder_name)
            self.folder_locale_name = self.folder.split('/')[-1:][0]
            self.folder_name = self.folder_locale_name
            self.folder_identifier = None
        else:
            self.folder = self.folder_locale_name = ALLMAIL
            self.folder_identifier = ALLMAIL

        if len(set([INBOX, SENT, DRAFTS, TRASH, SPAM]).intersection(set([self.folder]))) > 0:
            folder_flag = set([INBOX, SENT, DRAFTS, TRASH, SPAM]).intersection(set([self.folder])).pop()
            self.folder_name = None
            self.folder_locale_name = None
            self.folder_identifier = folder_flag

        if self.folder_locale_name is not None:
            # Check if folder is from account
            folder_found = False
            for account in accounts_qs:
                for folder_name, folder in account.folders.items():
                    if folder_name == self.folder_locale_name:
                        folder_found = True
                        break

                    children = folder.get('children', {})
                    for sub_folder_name, sub_folder in children.items():
                        if sub_folder_name == self.folder_locale_name:
                            folder_found = True
                            break

            if not folder_found:
                raise Http404()

        # Scrape messages together from one or more e-mail accounts
        search_criteria = parse_search_keys(kwargs.get('search_key'))
        self.imap_search_in_accounts(search_criteria, accounts=accounts_qs)

        return super(EmailSearchView, self).get(request, *args, **kwargs)

    def imap_search_in_accounts(self, search_criteria, accounts):
        """
        Perform a search on given or all accounts and save the results.
        """
        def get_uids_from_local(uids, account):
            qs = EmailMessage.objects.none()
            if self.folder_name is not None and self.folder_identifier is not None:
                qs = EmailMessage.objects.filter(Q(folder_identifier=self.folder_identifier) | Q(folder_name=self.folder_name))
            elif self.folder_name is not None:
                qs = EmailMessage.objects.filter(folder_name__in=[self.folder_name, self.folder])
            elif self.folder_identifier is not None:
                qs = EmailMessage.objects.filter(folder_identifier=self.folder_identifier)
            return qs.filter(account=account, uid__in=uids).order_by('-sent_date')

        self.resultset = []  # result set of email messages pks

        # Find corresponding messages in database and save message pks
        for account in accounts:
            server = None
            try:
                host = account.provider.imap_host
                port = account.provider.imap_port
                ssl = account.provider.imap_ssl
                server = IMAP(host, port, ssl)
                server.login(account.username,  account.password)

                folder = server.get_folder(self.folder_identifier or self.folder)
                total_count, uids = server.get_uids(folder, search_criteria)

                if len(uids):
                    qs = get_uids_from_local(uids, account)
                    if len(qs) == 0:
                        # Get actual messages for *uids* from *server* when they're not available locally
                        modifiers = ['BODY.PEEK[HEADER.FIELDS (Reply-To Subject Content-Type To Cc Bcc Delivered-To From Message-ID Sender In-Reply-To Received Date)]', 'FLAGS', 'RFC822.SIZE', 'INTERNALDATE']
                        folder_messages = server.get_messages(uids, modifiers, folder)

                        if len(folder_messages) > 0:
                            save_email_messages(folder_messages, account, folder, new_messages=True)

                    pks = qs.values_list('pk', flat=True)
                    self.resultset += list(pks)
            finally:
                if server:
                    server.logout()

    def get_queryset(self):
        """
        Return all messages matching the result set.
        """
        return EmailMessage.objects.filter(pk__in=self.resultset).extra(select={
            'num_attachments': 'SELECT COUNT(*) FROM email_emailattachment WHERE email_emailattachment.message_id = email_emailmessage.message_ptr_id AND inline=False'

        }).order_by('-sent_date')

    def get_context_data(self, **kwargs):
        """
        Overloading super().get_context_data to reflect the folder being searched in.
        """
        kwargs = super(EmailSearchView, self).get_context_data(**kwargs)
        kwargs.update({
            'list_title': _('%s for %s') % (self.folder_locale_name, kwargs.get('list_title'))
        })
        return kwargs


class EmailAttachmentProxy(View):
    def get(request, *args, **kwargs):
        pk = kwargs.get('pk')

        try:
            attachment = EmailAttachment.objects.get(pk=pk)
        except:
            raise Http404()

        s3_file = default_storage._open(attachment.attachment.name)

        wrapper = FileWrapper(s3_file)
        response = HttpResponse(wrapper, content_type='%s' % s3_file.key.content_type)
        response['Content-Disposition'] = 'attachment; filename=%s' % get_attachment_filename_from_url(s3_file.name)
        response['Content-Length'] = attachment.size
        return response

class EmailAttachmentRemoval(View):
    def get(request, *args, **kwargs):
        """
        Removal of attachments from drafts.
        """
        message_pk = kwargs.get('pk')
        attachment_pk = kwargs.get('attachment_pk')

        attachment = EmailAttachment.objects.filter(message_id=message_pk, pk=attachment_pk).all()
        if len(attachment) > 0:
            attachment.delete()

        return redirect(reverse('messaging_email_compose', kwargs={'pk': message_pk}))


# E-mail folder views
email_inbox_view = login_required(EmailInboxView.as_view())
email_sent_view = login_required(EmailSentView.as_view())
email_drafts_view = login_required(EmailDraftsView.as_view())
email_trash_view = login_required(EmailTrashView.as_view())
email_spam_view = login_required(EmailSpamView.as_view())
email_account_folder_view = login_required(EmailFolderView.as_view())

# Ajax views
email_html_view = login_required(EmailMessageHTMLView.as_view())
email_json_view = login_required(EmailMessageJSONView.as_view())
history_list_email_json_view = login_required(HistoryListEmailMessageJSONView.as_view())
mark_read_view = login_required(MarkReadAjaxView.as_view())
mark_unread_view = login_required(MarkUnreadAjaxView.as_view())
move_trash_view = login_required(MoveTrashAjaxView.as_view())

# E-mail interaction views
email_compose_view = login_required(EmailCreateView.as_view())
email_body_preview_view = login_required(EmailBodyPreviewView.as_view())
email_reply_view = login_required(EmailReplyView.as_view())
email_forward_view = login_required(EmailForwardView.as_view())
move_messages_view = login_required(MoveMessagesView.as_view())

# E-mail account wizard views
email_configuration_wizard_template = login_required(EmailConfigurationWizardTemplate.as_view())
email_configuration_wizard = login_required(EmailConfigurationView.as_view([EmailConfigurationStep1Form, EmailConfigurationStep2Form, EmailConfigurationStep3Form]))
email_share_wizard = login_required(EmailShareView.as_view())

edit_email_account_view = login_required(EditEmailAccountView.as_view())
detail_email_account_view = login_required(DetailEmailAccountView.as_view())

# E-mail templating views
list_email_template_view = login_required(ListEmailTemplateView.as_view())
add_email_template_view = login_required(AddEmailTemplateView.as_view())
edit_email_template_view = login_required(EditEmailTemplateView.as_view())
parse_email_template_view = login_required(ParseEmailTemplateView.as_view())

# other
email_search_view = login_required(EmailSearchView.as_view())
email_proxy_view = login_required(EmailAttachmentProxy.as_view())
email_attachment_removal = login_required(EmailAttachmentRemoval.as_view())
email_folder_sync_view = login_required(ForceFolderSyncView.as_view())