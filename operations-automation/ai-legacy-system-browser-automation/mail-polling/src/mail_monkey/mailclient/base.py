
from abc import ABC, abstractmethod

class BaseClient(ABC):
    """docstring for BaseClient"""
    def __init__(self):
        super(BaseClient, self).__init__()

    @abstractmethod
    def get_folder(self, name):
        """ get an outlook folder by name
        """
        pass 

    @abstractmethod
    def get_events(self, name):
        pass 

    @abstractmethod
    def create_message(subject, content, to, cc=None, bcc=None, send=False, show=True, activate=True, open=True):
        pass 


class BaseFolder(ABC):
    """docstring for BaseFolder"""
    def __init__(self):
        super(BaseFolder, self).__init__()

    @abstractmethod
    def get_messages(self):
        pass 

    @abstractmethod
    def get_name(self):
        pass 


class BaseMessage(ABC):
    """docstring for BaseMail"""
    def __init__(self):
        super(BaseMessage, self).__init__()

    @abstractmethod
    def get_sender(self):
        pass 

    @abstractmethod
    def get_recipients(self):
        pass 

    @abstractmethod
    def get_content(self, plain=True):
        pass 

    @abstractmethod
    def get_subject(self):
        pass 

    @abstractmethod
    def get_modification_date(self):
        pass 

    @abstractmethod
    def get_time_sent(self):
        pass 

    @abstractmethod
    def get_folder(self):
        pass 

    @abstractmethod
    def open(self):
        pass 

    @abstractmethod
    def activate(self):
        pass 

    @abstractmethod
    def was_sent(self):
        self.message.was_sent()


class BaseEvent(BaseMessage):
    """docstring for BaseMail"""
    def __init__(self):
        super(BaseEvent, self).__init__()
