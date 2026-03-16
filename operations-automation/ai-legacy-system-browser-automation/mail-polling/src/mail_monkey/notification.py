import os
from sys import platform
from subprocess import check_call #nosec B404

if platform == "linux" or platform == "linux2": # linux
    raise NotImplemented('linux not supported')

elif platform == "darwin": # MAC OS X

    # def notification(msg, subtitle="",   title="Mail Monkey", sound="Pop"):
    #     """ show a display notif, credits to stack overfolow
    #     """
    #     msg='display notification "%s" with title "%s" subtitle "%s" sound name "%s"' % (msg, title, subtitle, sound)
    #     check_call(['osascript',  '-e', msg])

    def notification(msg, subtitle="", title="Mail Monkey", sound="Pop", 
        icon=os.path.join(os.path.dirname(__file__), "mailmonkey.png"), image=None, delay=0):
        try:
            advanced_notification(msg, subtitle, title, sound, icon, image, delay)
        except:
            simple_notification(msg, subtitle, title, sound)
    

    def simple_notification(msg, subtitle="",   title="Roadmap Helper", sound="Pop"):
        """ show a display notif, credits to stack overfolow
        """
        msg='display notification "%s" with title "%s" subtitle "%s" sound name "%s"' % (msg, title, subtitle, sound)
        check_call(['osascript',  '-e', msg]) #nosec B607, B603


    def advanced_notification(msg, subtitle="", title="Mail Monkey", sound="Pop", 
        icon=os.path.join(os.path.dirname(__file__), "mailmonkey.png"), image=None, delay=0):

        import AppKit
        import Foundation

        notification = Foundation.NSUserNotification.alloc().init()
        notification.setTitle_(title)
        notification.setSubtitle_(subtitle)
        notification.setInformativeText_(msg)
        if icon:
           source_img = AppKit.NSImage.alloc().initByReferencingFile_(icon)
           notification.set_identityImage_(source_img)
        if image:
           source_img = AppKit.NSImage.alloc().initByReferencingFile_(image)
           notification.setContentImage_(source_img)
        if sound:
           notification.setSoundName_(sound)
        notification.setDeliveryDate_(Foundation.NSDate.dateWithTimeInterval_sinceDate_(delay, Foundation.NSDate.date()))
        Foundation.NSUserNotificationCenter.defaultUserNotificationCenter().scheduleNotification_(notification)


elif platform == "win32" or platform == "win64": # Windows 

    from plyer import notification as plyer_notification
    def notification(msg, subtitle="", title="Mail Monkey", sound="Pop"):
        # Combine title and subtitle if subtitle is provided
        full_title = f"{title}: {subtitle}" if subtitle else title
        
        plyer_notification.notify(
            title=full_title,
            message=msg,
            app_icon=os.path.join(os.path.dirname(__file__), "mailmonkey.ico"),
            timeout=10,  # Duration in seconds
        )

else: 
    raise NotImplemented(f'Unknown platform {platform}' )
