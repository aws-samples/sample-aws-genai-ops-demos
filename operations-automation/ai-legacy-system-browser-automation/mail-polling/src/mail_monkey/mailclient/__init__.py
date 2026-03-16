from sys import platform

def get_mailclient(classname=None, *args, **kvargs):

	if platform == "darwin":     # MAC OS X
		from .appscript_outlook import AppscriptOutlookClient
		return AppscriptOutlookClient( *args, **kvargs)
	elif platform == "win32" or platform == "win64": # Windows 
		from .win32_outlook import Win32OutlookClient
		return Win32OutlookClient( *args, **kvargs)
