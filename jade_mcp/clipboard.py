"""Temporarily supply XY text while retaining every existing Windows clipboard format."""
import ctypes
from ctypes import wintypes
import time


class TemporaryClipboardText:
    def __init__(self,text):
        self.text=text
        self.saved=[]
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.ole=ctypes.WinDLL('ole32')
        for name,args,result in [
            ('GetClipboardData',[wintypes.UINT],wintypes.HANDLE),
            ('SetClipboardData',[wintypes.UINT,wintypes.HANDLE],wintypes.HANDLE),
            ('OpenClipboard',[wintypes.HWND],wintypes.BOOL),
            ('EnumClipboardFormats',[wintypes.UINT],wintypes.UINT),
            ('GetClipboardSequenceNumber',[],wintypes.DWORD)]:
            fn=getattr(self.user,name);fn.argtypes=args;fn.restype=result
        self.ole.OleDuplicateData.argtypes=[wintypes.HANDLE,wintypes.UINT,wintypes.UINT]
        self.ole.OleDuplicateData.restype=wintypes.HANDLE
        self.kernel.GlobalFree.argtypes=[wintypes.HGLOBAL]
        self.gdi=ctypes.WinDLL('gdi32')
        self.gdi.CopyEnhMetaFileW.argtypes=[wintypes.HANDLE,wintypes.LPCWSTR]
        self.gdi.CopyEnhMetaFileW.restype=wintypes.HANDLE
        self.gdi.DeleteEnhMetaFile.argtypes=[wintypes.HANDLE]

    def _open(self):
        deadline=time.monotonic()+2
        while not self.user.OpenClipboard(None):
            if time.monotonic()>deadline:raise RuntimeError('Clipboard is busy; no clipboard change was made')
            time.sleep(.05)

    def _release(self):
        import win32gui
        for fmt,handle in self.saved:
            if fmt in {2,9}:win32gui.DeleteObject(handle) # CF_BITMAP / CF_PALETTE
            elif fmt==14:self.gdi.DeleteEnhMetaFile(handle)
            elif fmt==3:
                class Medium(ctypes.Structure):
                    _fields_=[('tymed',wintypes.DWORD),('handle',wintypes.HANDLE),('releaser',ctypes.c_void_p)]
                medium=Medium(32,handle,None) # TYMED_MFPICT
                self.ole.ReleaseStgMedium.argtypes=[ctypes.POINTER(Medium)]
                self.ole.ReleaseStgMedium(ctypes.byref(medium))
            else:self.kernel.GlobalFree(handle)
        self.saved=[]

    def __enter__(self):
        import win32clipboard
        self._open()
        emptied=False
        try:
            fmt=0
            while True:
                fmt=self.user.EnumClipboardFormats(fmt)
                if not fmt:break
                original=self.user.GetClipboardData(fmt)
                clone=(self.gdi.CopyEnhMetaFileW(original,None) if fmt==14 else self.ole.OleDuplicateData(original,fmt,2)) if original else None
                if not clone:raise RuntimeError('Cannot preserve a clipboard format; clipboard was left unchanged')
                self.saved.append((fmt,clone))
            win32clipboard.EmptyClipboard()
            emptied=True
            win32clipboard.SetClipboardText(self.text,13)
        except BaseException:
            if emptied:
                self.user.EmptyClipboard()
                self.saved=[(fmt,handle) for fmt,handle in self.saved if not self.user.SetClipboardData(fmt,handle)]
            self._release()
            raise
        finally:
            self.user.CloseClipboard()
        self.sequence=self.user.GetClipboardSequenceNumber()
        return self

    def __exit__(self,*_):
        self._open()
        try:
            if self.user.GetClipboardSequenceNumber()!=self.sequence:
                self._release()
                return
            self.user.EmptyClipboard()
            remaining=[]
            for fmt,handle in self.saved:
                if not self.user.SetClipboardData(fmt,handle):remaining.append((fmt,handle))
            self.saved=remaining
            if remaining:raise RuntimeError('Clipboard restore partially failed; retained duplicate handles remain in process')
        finally:
            self.user.CloseClipboard()
