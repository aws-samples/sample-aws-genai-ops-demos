import win32com.client as win32   
from win32com.client import Dispatch


#https://www.reddit.com/r/learnpython/comments/dq8o7v/python_outlook_html_formatting/ 


from .base import BaseClient, BaseFolder, BaseMessage, BaseEvent


#https://stackoverflow.com/a/22907769
class Oli():
    def __init__(self, outlook_object):
        self._obj = outlook_object

    def items(self):
        array_size = self._obj.Count
        for item_index in xrange(1,array_size+1):
            yield (item_index, self._obj[item_index])

    def prop(self):
        return sorted( self._obj._prop_map_get_.keys() )


class Win32OutlookClient(BaseClient):
    """docstring for BaseClient"""
    def __init__(self):
        super(Win32OutlookClient, self).__init__()
        self.app = Dispatch("Outlook.Application")
        self.outlook = self.app.GetNamespace("MAPI")
        self.folders = []
        self._default_folders = {
            'calendar': 9,
            'contacts': 10,
            'deleteditems': 3,
            'drafts': 16,
            'inbox': 6,
            'journal': 11,
            'junk': 3,
            'localfailures': 21,
            'managedemail': 29,
            'notes': 12,
            'outbox': 4,
            'sentmail': 5,
            'sent_items': 5, 
            'suggestedcontacts': 30,
            'syncissues': 20,
            'tasks': 13,
            'todo': 8,
            'rssfeeds': 25,
        }
        self._update_folders_list()

    def _processfolder_recursively(self, folder):
        self.folders.append(Win32OutlookFolder(folder))        
        for subfolder in folder.Folders:
            self._processfolder_recursively(subfolder)


    def _update_folders_list(self):
        self.folders = []
        inbox = self.outlook.GetDefaultFolder(self._default_folders.get("inbox"))

        self._processfolder_recursively(inbox)

    def get_folder(self, name):
        """ get an outlook folder by name
        """
        name = name.lower().strip()

        if name in  self._default_folders:
            return Win32OutlookFolder(self.outlook.GetDefaultFolder(self._default_folders.get(name)))

        for folder in self.folders:
            if name == str(folder.get_name()).lower().strip():
                return folder
        
        raise Exception('Cannot find folder with a name %r' % name )

    def get_events(self, name):
        raise NotImplemented()
        for calendar in self.outlook.calendar():
            for event in calendar.calendar_events():
                yield Win32OutlookEvent(event)
        return 


    def create_message(self, subject, content, to, cc=None, bcc=None, send=False, show=True, activate=True, open=True):
        """ create message and optionally send it
        """
        olFormatHTML = 2
        olFormatPlain = 1
        olFormatRichText = 3
        olFormatUnspecified = 0
        olMailItem = 0x0

        msg = self.app.CreateItem(olMailItem)
        msg.To = "; ".join(to)
        if cc:
            msg.CC = "; ".join(cc)
        if bcc:
            msg.BCC = "; ".join(bcc)

        msg.Subject = subject
        msg.HtmlBody = content
        msg.BodyFormat = olFormatHTML


        # attachment1 = os.getcwd() +"\\file.ini"
        # msg.Attachments.Add(attachment1)

        message = Win32OutlookMessage(msg)

        if open: message.open()
        if activate: message.activate()
        if send: message.send()

        return message

    def create_event(self, subject='', content='', category=None, is_private=False, end_time=None, start_time=None, open=False, activate=False, send=False):
        olAppointmentItem = 1
        event = self.app.CreateItem(olAppointmentItem)
        event.subject = subject
        event.body = content

        event.MeetingStatus = 1
        # if recipients:
        #     event.Recipients.Add(recipient)
        #     event.Recipients.ResolveAll()

        end_time = end_time or datetime.now().replace(microsecond=0, second=0, minute=0)
        start_time = start_time or (end_time - timedelta(minutes=15))

        event.start = start_time.strftime('%Y-%m-%d %H:%M')
        event.duration =  (end_time - start_time).total.minutes
        event.save()

        if open: event.open()
        if activate: event.activate()
        if send: event.send()

        return Win32OutlookEvent(event)


class Win32OutlookFolder(BaseFolder):
    """docstring for BaseFolder"""
    def __init__(self, folder):
        super(Win32OutlookFolder, self).__init__()
        self.folder = folder

    def get_messages(self, orderby="ReceivedTime", limit=None, maxcount=None):
        """ get messages from folder
        """
        messages = self.folder.Items

        messages.Sort("[%s]" % orderby, False)
        message = messages.GetLast()
        count = 1
        while message:
            yield Win32OutlookMessage(message)
            message = messages.GetPrevious()
            count += 1


    def get_name(self):
        """ get name of folder
        """
        return self.folder.Name

class Win32OutlookMessage(BaseMessage):
    """docstring for BaseMail"""
    def __init__(self, message):
        super(Win32OutlookMessage, self).__init__()
        self.message = message

    def get_sender(self):

        olMail = 43 # https://docs.microsoft.com/en-us/office/vba/api/outlook.olobjectclass
        if self.message.Class==olMail:
            if self.message.SenderEmailType=='EX':
                user = self.message.Sender.GetExchangeUser()
                return None if not user else user.PrimarySmtpAddress
            else:
                return self.message.SenderEmailAddress

        return None

    def get_recipients(self):
        """ returns message recipients dict: {'to': ['e@ma.il'], 'cc': ['e@ma.il']}
        """
        res = {}
        for recipient in self.message.Recipients:
            email_address = recipient.Address
            recipient_type = {
                3: 'bcc',
                2: 'cc',
                0: 'originator',
                1: 'to',
            }.get(recipient.Type)

            if recipient_type not in res:
                res[recipient_type] = []
            res[recipient_type].append(email_address)

        return {key:sorted(value) for key, value in res.items()}


    def get_content(self, plain=False):
        if plain:
            return self.message.body
        else:
            return self.message.HTMLBody

    def get_subject(self):
        try:
            return self.message.Subject
        except Exception as exc:
            return ''

    def get_modification_date(self):
        return self.message.LastModificationTime.replace(tzinfo=None)  

    def get_time_sent(self):
        return self.message.SentOn.replace(tzinfo=None)

    def get_time_recieved(self):
        return self.message.ReceivedTime

    def get_folder(self):
        return Win32OutlookFolder(self.message.parent) 

    def open(self):
        modal = False
        self.message.Display(modal)

    def activate(self):
        pass

    def was_sent(self):
        return self.message.Sent

    def send(self):
        return self.message.Send()


class Win32OutlookEvent():
    """docstring for BaseEvent"""
    def __init__(self, event):
        self.event = event
        
