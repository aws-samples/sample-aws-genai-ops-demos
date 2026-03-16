from datetime import datetime, timedelta
from mactypes import Alias
from subprocess import Popen, PIPE, STDOUT #nosec B404

from bs4 import BeautifulSoup
from appscript import app, k, its # pip3 install appscript
from appscript.reference import CommandError

from .base import BaseClient, BaseFolder, BaseMessage, BaseEvent

class AppscriptOutlookClient(BaseClient):
    """docstring for BaseClient"""
    def __init__(self):
        super(AppscriptOutlookClient, self).__init__()
        self.outlook = app('Microsoft Outlook')

        # When using in cron Python is unable to access Outlook
        #   until user hits "accept" on a message box.
        try:
            self.outlook.folders.name() # testing if outlook is reachable
        except CommandError as exc:
            if (exc.errormessage == "The user has declined permission."):
                print ("WARNING: Python cannot get permissions to control outlook out of this environment")
                print ("INFO: Will run a basic osascript now. Please hit accept in a messagebox when you see it to allow the control of Outlook")
                proc = Popen(['osascript'], stdout=PIPE, stdin=PIPE, stderr=PIPE) #nosec B607, B603
                stdout_data = proc.communicate(input=b"""
                    tell application  "Microsoft Outlook"
                        set selected folder to inbox of exchange account 1
                        set item list sort of main window 1 to {sort field:sent time sort, ascending:false} -- or true
                        set sorted in groups of main window 1 to true -- or false
                    end tell
                    """
                )[0]

                self.outlook.folders.name()
                print ("INFO: Python has access to Outlook now.")
            else:
                raise


    def get_folder(self, name):
        """ get an outlook folder by name
        """
        name = name.lower().strip()
        try:
            return AppscriptOutlookFolder(getattr(self.outlook, name))
        except AttributeError:
            names = self.outlook.folders.name()
            names = list(map(lambda s: str(s).lower(), names))

            if name in names:
                return AppscriptOutlookFolder(self.outlook.folders[names.index(name)+1])

        raise Exception('Cannot find folder with a name %r' % name )

    def get_events(self, name):
        for calendar in self.outlook.calendar():
            for event in calendar.calendar_events():
                yield AppscriptOutlookEvent(event)
        return 


    def create_message(self, subject, content, to, cc=None, bcc=None, send=False, show=True, activate=True, open=True, attachments=[]):
        """ create message and optionally send it
        """
        msg = self.outlook.make(
            new=k.outgoing_message,
            with_properties={
                k.subject: subject,
                k.content: content
            },
        )
        for email in to or []:
            msg.make(
                new=k.to_recipient,
                with_properties={k.email_address: {k.address: email}}
            )

        for email in cc or []:
            msg.make(
                new=k.cc_recipient,
                with_properties={k.email_address: {k.address: email}}
            )

        for email in bcc or []:
            msg.make(
                new=k.bcc_recipient,
                with_properties={k.email_address: {k.address: email}}
            )

        for attachment_file in attachments:
            msg.make(
                new=k.attachment,
                with_properties={
                    k.file: Alias(str(attachment_file))
                }
            )

        if open: msg.open()
        if activate: msg.activate()
        if send: msg.send()

        return AppscriptOutlookMessage(msg)

    def create_event(self, subject='', content='', category=None, is_private=False, end_time=None, start_time=None, open=False, activate=False, send=False):
        event = self.outlook.make(
            new=k.calendar_event,
            with_properties={
                k.subject: subject, 
                k.content: content,
                k.category: category or [],
                k.is_private: is_private,
                k.end_time: end_time or (datetime.now() + timedelta(minutes=15)),
                k.start_time: start_time or datetime.now(),
            }
        )
        if open: event.open()
        if activate: event.activate()
        if send: event.send()
        return AppscriptOutlookEvent(event)



class AppscriptOutlookFolder(BaseFolder):
    """docstring for BaseFolder"""
    def __init__(self, folder):
        super(AppscriptOutlookFolder, self).__init__()
        self.folder = folder

    def get_messages(self, start=None, end=None):
        """ get messages from folder
        """
        messages = self.folder.messages

        if start and end:
            messages = messages[(its.modification_date >= start).AND(its.modification_date <= end)]

        for message in messages():
            yield AppscriptOutlookMessage(message)


    def get_name(self):
        """ get name of folder
        """
        return self.folder.name()

class AppscriptOutlookMessage(BaseMessage):
    """docstring for BaseMail"""
    def __init__(self, message):
        super(AppscriptOutlookMessage, self).__init__()
        self.message = message

    def get_sender(self):
        try:
            return self.message.sender().get(k.address, 'unknown')
        except Exception as exc:
            return None

    def get_recipients(self):
        """ returns message recipients dict: {'to': ['e@ma.il'], 'cc': ['e@ma.il']}
        """
        res = {}
        for recipient in self.message.recipients():
            properties = recipient.properties()
            recipient_type = properties[k.type].name.split('_')[0]
            email_address = properties[k.email_address][k.address]
            if recipient_type not in res:
                res[recipient_type] = []
            res[recipient_type].append(email_address)
        return {key:sorted(value) for key, value in res.items()}


    def get_content(self, plain=False):
        if plain:
            return self.message.plain_text_content()
        else:
            return self.message.content()

    def get_subject(self):
        try:
            return self.message.subject()
        except Exception as exc:
            return ''

    def get_modification_date(self):
        return self.message.modification_date()

    def get_time_sent(self):
        return self.message.time_sent()

    def get_folder(self):
        return AppscriptOutlookFolder(self.message.folder())

    def open(self):
        self.message.open()

    def activate(self):
        self.message.activate()

    def was_sent(self):
        return self.message.was_sent()

    def send(self):
        self.message.send()


    def reply_to(self,opening_window=False, reply_to_all=True):
        return self.message.reply_to(opening_window=opening_window, reply_to_all=reply_to_all)

    def compose_response(self, html_message):
        msg = self.message.reply_to(opening_window=False, reply_to_all=True)
        content = msg.content().replace('\r', '\n')
        bs = BeautifulSoup(content, features="lxml")
        bs.body.insert(0, BeautifulSoup(f'<div>{html_message}</div>', features="lxml").body.div)
        msg.content.set(str(bs))
        msg.open()
        msg.activate()
        return msg

class AppscriptOutlookEvent():
    """docstring for BaseMail"""
    def __init__(self, event):
        self.event = event
