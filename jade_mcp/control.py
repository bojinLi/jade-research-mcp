"""Process-scoped Windows automation for JADE. Never resolves arbitrary desktop windows."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time

import psutil
from pywinauto import Desktop
from pywinauto.controls.hwndwrapper import HwndWrapper

DEFAULT_EXE = r"E:\MDI Jade 9\jade9c.exe"


class JadeController:
    def __init__(self):
        self.exe = Path(os.environ.get("JADE_EXE", DEFAULT_EXE)).resolve()
        self._snapshot = None

    def processes(self):
        results = []
        for process in psutil.process_iter(["pid", "name", "exe"]):
            try:
                if process.info["exe"] and Path(process.info["exe"]).resolve() == self.exe:
                    results.append({"pid": process.pid, "name": process.info["name"], "exe": str(self.exe)})
            except (psutil.Error, OSError):
                continue
        return results

    def _windows(self):
        pids = {p["pid"] for p in self.processes()}
        return [w for w in Desktop(backend="win32").windows(visible_only=True)
                if w.process_id() in pids and w.rectangle().width()>0 and w.rectangle().height()>0
                and w.rectangle().bottom>0]

    def _wrap(self, handle):
        if not handle:
            raise ValueError("Observe jade_state and choose an explicit window handle")
        try:
            window = HwndWrapper(int(handle))
            pid = window.process_id()
            process = psutil.Process(pid)
            if Path(process.exe()).resolve() != self.exe:
                raise ValueError("Handle does not belong to configured JADE executable")
            if not window.is_visible():
                raise ValueError("JADE target is not visible")
            return window
        except (psutil.Error, RuntimeError) as exc:
            raise ValueError("Window no longer exists; observe jade_state again") from exc

    @staticmethod
    def _describe(control):
        rect = control.rectangle()
        data = {"handle": int(control.handle), "pid": control.process_id(), "text": control.window_text(),
                "class_name": control.class_name(), "control_id": control.control_id(),
                "enabled": control.is_enabled(), "visible": control.is_visible(),
                "rect": [rect.left, rect.top, rect.right, rect.bottom]}
        if data['class_name'] in {'StatusBar20WndClass','msctls_statusbar32'}:
            try:
                from pywinauto.controls.common_controls import StatusBarWrapper
                data['status_panels']=StatusBarWrapper(control.handle).texts()[:60]
            except Exception as exc:data['status_read_error']=type(exc).__name__
        return data

    def _tree(self):
        windows = []
        for window in self._windows():
            item = self._describe(window)
            # Bound traversal so a large JADE result list cannot flood the MCP context.
            descendants = window.descendants()
            item["controls"] = [self._describe(c) for c in descendants[:350] if c.is_visible()]
            item["controls_truncated"] = len(descendants) > 350
            windows.append(item)
        return windows

    def state(self):
        tree = self._tree()
        stamp = hashlib.sha256(json.dumps(tree, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        token = hashlib.sha256(f"{stamp}:{time.monotonic_ns()}".encode()).hexdigest()[:24]
        self._snapshot = {"token": token, "stamp": stamp, "created": time.monotonic(), "tree": tree}
        return {"executable": str(self.exe), "installed": self.exe.is_file(), "processes": self.processes(),
                "windows": tree, "observation_token": token, "token_valid_seconds": 90,
                "note": "Observe before each action. Actions are dispatched, not proof that an analysis finished."}

    def _check(self, token):
        observation = self._snapshot
        if not observation or token != observation["token"] or time.monotonic()-observation["created"] > 90:
            raise ValueError("Missing/expired observation; call jade_state again")
        tree = self._tree()
        stamp = hashlib.sha256(json.dumps(tree, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if stamp != observation["stamp"]:
            self._snapshot = None
            raise ValueError("JADE UI changed after observation; call jade_state again")
        return observation

    def _consume(self):
        self._snapshot = None

    @staticmethod
    def _actionable(control):
        import win32gui
        # GA_ROOT follows child-parent links but not dialog ownership. GA_ROOTOWNER would
        # incorrectly reject enabled modal-dialog controls because their owner is disabled.
        root=win32gui.GetAncestor(control.handle,2)
        if not control.is_enabled() or not HwndWrapper(root).is_enabled():
            raise ValueError('JADE target is disabled or blocked by a modal dialog; observe and handle that dialog first')

    @staticmethod
    def _post_button(control, right=False):
        import win32gui
        HwndWrapper(win32gui.GetAncestor(control.handle,2)).set_focus()
        # GetClientRect is in the target's own logical units, including DPI-unaware VB6 windows.
        left,top,client_right,client_bottom=win32gui.GetClientRect(control.handle)
        control.click(button='right' if right else 'left',coords=(client_right//2,client_bottom//2))

    @staticmethod
    def _launch_from_shell(exe):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            shell_windows=win32com.client.Dispatch('Shell.Application').Windows()
            # SWC_DESKTOP works even when the user has no Explorer folder open.
            # https://learn.microsoft.com/windows/win32/api/exdisp/nf-exdisp-ishellwindows-findwindowsw
            try:desktop=shell_windows.FindWindowSW(0,0,8,0,1)
            except pythoncom.com_error:desktop=None
            explorers=[desktop] if desktop is not None else [
                w for w in shell_windows if Path(str(w.FullName)).name.lower()=='explorer.exe']
            if not explorers:raise RuntimeError('No Windows desktop shell is available; unlock the interactive desktop and retry')
            explorers[0].Document.Application.ShellExecute(str(exe),'',str(exe.parent),'open',1)
        finally:pythoncom.CoUninitialize()

    def launch(self):
        if not self.exe.is_file() or self.exe.suffix.lower() != ".exe" or not re.fullmatch(r"jade[\w-]*",self.exe.stem,re.I):
            raise ValueError("Set JADE_EXE to the installed JADE executable")
        if not self.processes():
            # MCP's Windows stdio client terminates its server process tree on disconnect.
            # Ask the existing desktop shell to launch the installed GUI app so JADE is
            # not a child of the short-lived MCP server and survives reconnection.
            self._launch_from_shell(self.exe)
            deadline = time.monotonic()+20
            while time.monotonic()<deadline:
                if any(w.window_text().startswith('Jade ') for w in self._windows()): break
                time.sleep(0.25)
        # A minimized VB6 main window has a negative off-screen rectangle and is
        # intentionally excluded from actionable observations. Explicit connect/launch
        # brings that existing JADE window back so the client can observe it again.
        pids={p['pid'] for p in self.processes()}
        for window in Desktop(backend='win32').windows(visible_only=True):
            if window.process_id() in pids and window.window_text().startswith('Jade ') and window.is_minimized():
                window.restore()
        return self.state()

    @staticmethod
    def _menu_tree(menu, prefix=None, depth=0):
        if not menu or depth>8:
            return []
        result=[]
        for item in menu.items():
            text=item.text()
            indexes=(prefix or [])+[item.index()]
            record={"text":text,"index_path":indexes,"command_id":item.item_id(),
                    "enabled":item.is_enabled(),"checked":item.is_checked()}
            submenu=item.sub_menu()
            if submenu:
                record["children"]=JadeController._menu_tree(submenu,indexes,depth+1)
            result.append(record)
        return result

    def menus(self, window_handle):
        window=self._wrap(window_handle)
        result=self._menu_tree(window.menu())
        return {"window_handle":window_handle,"menus":result,
                "note":"Choose a leaf index_path from this live menu. Disabled items cannot run."}

    def find_menu(self, window_handle, query):
        terms=[term.strip().lower() for term in query.split('|') if term.strip()]
        if not terms: raise ValueError("Supply a menu keyword; use | for alternatives")
        matches=[]
        def walk(items, captions):
            for item in items:
                full=captions+[item['text']]
                if 'children' in item:
                    walk(item['children'],full)
                elif any(term in item['text'].lower() for term in terms):
                    matches.append({**item,'caption_path':full})
        walk(self.menus(window_handle)['menus'],[])
        return {'window_handle':window_handle,'query':query,'matches':matches}

    def menu(self, window_handle, index_path, observation_token):
        self._check(observation_token)
        window=self._wrap(window_handle)
        if not window.is_enabled(): raise ValueError('JADE window is blocked by a modal dialog; handle that dialog first')
        if not index_path or len(index_path)>8 or any(type(i)!=int or i<0 for i in index_path):
            raise ValueError("Use an index_path returned by jade_menus")
        menu=window.menu()
        for j,index in enumerate(index_path):
            if not menu or index>=menu.item_count(): raise ValueError("Menu path no longer exists")
            item=menu.item(index)
            if not item.is_enabled(): raise ValueError("Menu item is disabled in JADE")
            if j+1<len(index_path): menu=item.sub_menu()
        if item.sub_menu(): raise ValueError("Select a leaf command, not a submenu")
        caption=item.text()
        self._consume()
        # Post rather than SendMessage: modal dialog lifetime must not block the MCP request.
        window.post_message(0x111,item.item_id(),0)
        time.sleep(0.3)
        return {"dispatched_menu":caption,"index_path":index_path,"state":self.state()}

    def click_control(self, control_handle, observation_token, right=False):
        observation=self._check(observation_token)
        allowed={row["handle"] for w in observation["tree"] for row in w["controls"]}
        if control_handle not in allowed: raise ValueError("Control was not present in the last JADE observation")
        control=self._wrap(control_handle)
        self._actionable(control)
        if not control.is_enabled(): raise ValueError("Control is disabled")
        if not right and control.friendly_class_name() not in {"Button","CheckBox","RadioButton"}:
            raise ValueError("Only buttons/checkboxes/radio buttons support semantic click; use screenshot coordinates for custom controls")
        self._consume()
        self._post_button(control,right)
        time.sleep(0.2)
        return self.state()

    def select_item(self, control_handle, item_text, observation_token):
        observation=self._check(observation_token)
        allowed={row['handle'] for w in observation['tree'] for row in w['controls']}
        if control_handle not in allowed: raise ValueError('Control was not present in the last observation')
        control=self._wrap(control_handle)
        self._actionable(control)
        if control.friendly_class_name() not in {'ComboBox','ListBox'} or not control.is_enabled():
            raise ValueError('Target must be an enabled JADE ComboBox/ListBox')
        choices=control.item_texts()
        if choices.count(item_text)!=1: raise ValueError('Choose one exact unique item returned by jade_control_items')
        self._consume()
        control.select(item_text)
        return self.state()

    def control_items(self, control_handle):
        control=self._wrap(control_handle)
        if control.friendly_class_name() not in {'ComboBox','ListBox'}:
            raise ValueError('Target must be a JADE ComboBox/ListBox')
        return {'control_handle':control_handle,'items':control.item_texts()[:2000]}

    def set_text(self, control_handle, text, observation_token):
        observation=self._check(observation_token)
        allowed={row["handle"] for w in observation["tree"] for row in w["controls"]}
        if control_handle not in allowed: raise ValueError("Edit control was not present in the last observation")
        control=self._wrap(control_handle)
        self._actionable(control)
        if control.friendly_class_name()!="Edit" or not control.is_enabled():
            raise ValueError("Target must be an enabled JADE Edit control")
        if len(text)>10000 or "\0" in text: raise ValueError("Text too long or contains NUL")
        self._consume()
        control.set_edit_text(text)
        return self.state()

    def screenshot(self, window_handle):
        window=self._wrap(window_handle)
        # PrintWindow captures only the target window, avoiding other desktop windows in the returned image.
        import ctypes
        from ctypes import wintypes
        import win32ui
        import win32gui
        from PIL import Image
        rect=window.rectangle()
        width,height=rect.width(),rect.height()
        if width<=0 or height<=0 or width*height>25_000_000:
            raise ValueError("Invalid window bounds; restore the JADE window")
        hwnd=int(window.handle)
        user32=ctypes.WinDLL("user32",use_last_error=True)
        user32.GetWindowDpiAwarenessContext.argtypes=[wintypes.HWND]
        user32.GetWindowDpiAwarenessContext.restype=ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes=[ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype=ctypes.c_void_p
        previous_context=user32.SetThreadDpiAwarenessContext(user32.GetWindowDpiAwarenessContext(hwnd))
        logical_rect=win32gui.GetWindowRect(hwnd)
        capture_width,capture_height=logical_rect[2]-logical_rect[0],logical_rect[3]-logical_rect[1]
        hwnd_dc=win32gui.GetWindowDC(hwnd)
        source_dc=win32ui.CreateDCFromHandle(hwnd_dc)
        memory_dc=source_dc.CreateCompatibleDC()
        bitmap=win32ui.CreateBitmap()
        try:
            bitmap.CreateCompatibleBitmap(source_dc,capture_width,capture_height)
            memory_dc.SelectObject(bitmap)
            user32.PrintWindow.argtypes=[wintypes.HWND,wintypes.HDC,wintypes.UINT]
            user32.PrintWindow.restype=wintypes.BOOL
            if not user32.PrintWindow(hwnd,memory_dc.GetSafeHdc(),2):
                raise RuntimeError("JADE PrintWindow capture failed; no desktop fallback was used")
            img=Image.frombuffer("RGB",(capture_width,capture_height),bitmap.GetBitmapBits(True),"raw","BGRX",0,1)
            if img.size!=(width,height):
                img=img.resize((width,height),Image.Resampling.LANCZOS)
            stream=io.BytesIO()
            img.save(stream,format="PNG")
            return stream.getvalue()
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            memory_dc.DeleteDC()
            source_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd,hwnd_dc)
            if previous_context: user32.SetThreadDpiAwarenessContext(previous_context)

    def coordinate_click(self, window_handle, x, y, observation_token, double=False):
        self._check(observation_token)
        window=self._wrap(window_handle)
        self._actionable(window)
        rect=window.rectangle()
        if not 0<=x<rect.width() or not 0<=y<rect.height(): raise ValueError("Coordinates outside target window")
        self._consume()
        # Physical click only after process/bounds/staleness validation.
        window.set_focus()
        import win32gui
        import win32process
        target=win32gui.WindowFromPoint((rect.left+x,rect.top+y))
        if not target or win32process.GetWindowThreadProcessId(target)[1]!=window.process_id():
            raise RuntimeError('JADE target is occluded; no click was sent. Restore its window and observe again.')
        from pywinauto import mouse
        screen_point=(rect.left+x,rect.top+y)
        if double:mouse.double_click(coords=screen_point)
        else:mouse.click(coords=screen_point)
        return self.state()

    def key(self, window_handle, key, observation_token):
        self._check(observation_token)
        window=self._wrap(window_handle)
        self._actionable(window)
        keys={"enter":"{ENTER}","escape":"{ESC}","space":"{SPACE}","tab":"{TAB}","shift+tab":"+{TAB}",
              "up":"{UP}","down":"{DOWN}","left":"{LEFT}","right":"{RIGHT}",
              "ctrl+o":"^o","ctrl+a":"^a","f1":"{F1}","f2":"{F2}","f3":"{F3}",
              "f4":"{F4}","f5":"{F5}","f6":"{F6}","f7":"{F7}","f8":"{F8}","f9":"{F9}"}
        if key.lower() not in keys: raise ValueError(f"Supported keys: {', '.join(keys)}")
        self._consume()
        window.type_keys(keys[key.lower()],set_foreground=True)
        time.sleep(0.2)
        return self.state()

    def open_file(self, path, window_handle, observation_token, import_mode='native'):
        self._check(observation_token)
        source=Path(path).expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix.lower() not in {".mdi",".xy",".xye",".txt",".csv",".dat",".raw",".rd",".uxd",".xrdml",".brml",".cif",".wpf",".jip",".sav",".pft",".pks"}:
            raise ValueError("Choose an existing diffraction/structure data file")
        window=self._wrap(window_handle)
        self._actionable(window)
        if import_mode=='native':return self._open_native_file(source,window)
        if import_mode!='clipboard':raise ValueError('import_mode must be native or clipboard')
        if source.suffix.lower() in {'.mdi','.xy','.xye','.txt','.csv','.dat'}:
            if not window.is_enabled():raise ValueError('JADE is busy or blocked by a modal dialog; wait/observe before import')
            return self._paste_text_pattern(source,window)
        # JADE 9 implements file drop. Do not guess controls in its nonstandard file browser.
        import ctypes
        from ctypes import wintypes
        import struct
        import win32gui
        if not win32gui.GetWindowLong(window_handle,-20)&0x10:
            return self._open_jade9_browser(source,window)
        payload=struct.pack("<IiiII",20,0,0,0,1)+(str(source)+"\0\0").encode("utf-16-le")
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.GlobalAlloc.argtypes=[wintypes.UINT,ctypes.c_size_t]
        kernel.GlobalAlloc.restype=wintypes.HGLOBAL
        kernel.GlobalLock.argtypes=[wintypes.HGLOBAL]
        kernel.GlobalLock.restype=ctypes.c_void_p
        kernel.GlobalUnlock.argtypes=[wintypes.HGLOBAL]
        kernel.GlobalFree.argtypes=[wintypes.HGLOBAL]
        user=ctypes.WinDLL("user32",use_last_error=True)
        user.PostMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        user.PostMessageW.restype=wintypes.BOOL
        handle=kernel.GlobalAlloc(0x42,len(payload))
        if not handle: raise OSError("Cannot allocate file-drop buffer")
        address=kernel.GlobalLock(handle)
        if not address:
            kernel.GlobalFree(handle)
            raise OSError("Cannot lock file-drop buffer")
        ctypes.memmove(address,payload,len(payload))
        kernel.GlobalUnlock(handle)
        self._consume()
        if not user.PostMessageW(window_handle,0x233,handle,0):
            kernel.GlobalFree(handle)
            raise OSError("JADE rejected file-drop message")
        # On successful post JADE owns HDROP and releases it using DragFinish.
        time.sleep(0.5)
        return {"requested_file":str(source),"status":"file drop dispatched; verify loaded pattern in returned state/screenshot",
                "state":self.state()}

    def _open_native_file(self,source,main):
        """Use JADE's File -> Read Files common dialog, retaining native format and metadata."""
        matches=self.find_menu(main.handle,'文件读入|read files')['matches']
        matches=[m for m in matches if '\tCtrl+R' in m['text'] or 'read files' in m['text'].lower()]
        if len(matches)!=1:raise ValueError('Native File Read entry not found; inspect the installed JADE menus')
        before={w.handle for w in self._windows()}
        self._consume()
        main.post_message(0x111,matches[0]['command_id'],0)
        deadline=time.monotonic()+8
        dialog=None
        while time.monotonic()<deadline:
            candidates=[w for w in self._windows() if w.handle not in before and w.class_name()=='#32770']
            for candidate in candidates:
                edits=[c for c in candidate.descendants() if c.class_name()=='Edit' and c.control_id()==1148]
                if len(edits)==1:dialog=candidate;edit=edits[0];break
            if dialog:break
            time.sleep(.15)
        if dialog is None:
            return {'requested_file':str(source),'status':'file-dialog entry dispatched; expected common dialog not found','verified_loaded':False,'state':self.state()}
        self._actionable(edit)
        edit.set_edit_text(str(source))
        if edit.window_text()!=str(source):raise RuntimeError('File-name field did not retain the requested path')
        dialog.type_keys('{ENTER}',set_foreground=True)
        started=time.monotonic()
        while time.monotonic()-started<20:
            time.sleep(.2)
            windows=self._windows()
            if time.monotonic()-started>=1 and main.is_enabled() and not any(w.handle==dialog.handle for w in windows):break
            if any(w.handle not in before|{dialog.handle} and w.is_enabled() for w in windows):break
        observed=self.state()
        pending=[w for w in observed['windows'] if w['handle']!=main.handle and w['enabled'] and w['class_name']!='VBBubbleRT6']
        return {'requested_file':str(source),'source_sha256':__import__('hashlib').sha256(source.read_bytes()).hexdigest(),
                'method':'JADE native File Read common dialog','status':'native file-open submitted' if not pending else 'additional JADE dialog requires inspection',
                'file_dialog_closed':not any(w['handle']==dialog.handle for w in observed['windows']),
                'verified_loaded':False,'note':'Inspect the loaded file label, scan range, exported data or refinement result before asserting success.',
                'state':observed}

    def _paste_text_pattern(self,source,main):
        from .research import read_pattern
        from .clipboard import TemporaryClipboardText
        pattern=read_pattern(str(source))
        import numpy as np
        if not np.allclose(np.diff(pattern.x),np.median(np.diff(pattern.x)),rtol=.02,atol=1e-7):
            raise ValueError('JADE clipboard import requires an evenly sampled 2theta grid')
        if len(pattern.x)>100000: raise ValueError('JADE clipboard import is limited to 100,000 points')
        browser=next((w for w in self._windows() if w.window_text().startswith('读入衍射峰图文件')),None)
        if browser and browser.is_enabled():
            close=next((c for c in browser.children() if c.window_text()=='关闭(&C)'),None)
            if close:
                self._post_button(close)
                time.sleep(.2)
        matches=self.find_menu(main.handle,'主要峰图|primary')['matches']
        matches=[m for m in matches if any('粘贴' in caption or 'paste' in caption.lower() for caption in m['caption_path'])]
        if len(matches)!=1: raise ValueError('Cannot uniquely identify JADE Paste Primary Pattern menu')
        text='\r\n'.join(f'{x:.10g}\t{y:.10g}' for x,y in zip(pattern.x,pattern.y))+'\r\n'
        self._consume()
        with TemporaryClipboardText(text):
            main.post_message(0x111,matches[0]['command_id'],0)
            started=time.monotonic()
            while time.monotonic()-started<15:
                time.sleep(.25)
                if time.monotonic()-started>=3 and main.is_enabled():break
            observed=self.state()
        return {'requested_file':str(source),'method':'JADE Paste Primary Pattern from numerical XY columns',
                'input':pattern.metadata,'status':'paste dispatched; inspect JADE pattern and scan range',
                'note':'Vendor metadata are not imported through XY paste. Confirm JADE radiation/wavelength before JADE calculations. In the tested JADE 9 path intensities are rounded to integer counts; originals retain full precision.',
                'state':observed}

    def wait_ready(self,window_handle,timeout_seconds=15):
        if not 0<=timeout_seconds<=30:raise ValueError('timeout_seconds must be 0..30')
        target=self._wrap(window_handle)
        deadline=time.monotonic()+timeout_seconds
        while not target.is_enabled() and time.monotonic()<deadline:time.sleep(.25)
        return {'ready':target.is_enabled(),'state':self.state()}

    def _open_jade9_browser(self,source,main):
        """Installed JADE 9 Chinese VB6 profile, validated against live controls rather than screen positions."""
        import win32gui
        browsers=[w for w in self._windows() if w.window_text().startswith('读入衍射峰图文件')]
        if not browsers:
            matches=self.find_menu(main.handle,'衍射峰图')['matches']
            choices=[m for m in matches if '\tCtrl+O' in m['text']]
            if len(choices)!=1:
                raise ValueError('JADE version is not covered by the tested JADE 9 Chinese file-browser profile')
            self._consume()
            main.post_message(0x111,choices[0]['command_id'],0)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                browsers=[w for w in self._windows() if w.window_text().startswith('读入衍射峰图文件')]
                if browsers: break
                time.sleep(.1)
        if len(browsers)!=1: raise ValueError('Expected one JADE file browser; observe dialogs before retrying')
        browser=browsers[0]
        if not browser.is_enabled(): raise ValueError('A JADE modal dialog blocks its file browser')
        controls={c.control_id():c for c in browser.children()}
        location=controls.get(25)
        if not location or location.class_name()!='ThunderRT6ComboBox':
            raise ValueError('Unknown JADE file-browser layout; observe and use generic controls')
        self._consume()
        return {'requested_file':str(source),'status':'file browser opened; binary-file selection is a separate interactive step',
                'browser_handle':int(browser.handle),'state':self.state()}

    def read_table(self,control_handle,max_rows=100):
        from pywinauto.controls.common_controls import ListViewWrapper
        control=self._wrap(control_handle)
        if control.class_name() not in {'ListView20WndClass','SysListView32'}:
            raise ValueError('Target must be a JADE Win32/VB6 list-view table')
        if not 1<=max_rows<=1000:raise ValueError('max_rows must be 1..1000')
        table=ListViewWrapper(control.handle)
        count=table.item_count()
        columns=table.column_count()
        rows=[[table.get_item(i,j).text() for j in range(columns)] for i in range(min(count,max_rows))]
        return {'control_handle':control_handle,'row_count':count,'column_count':columns,'rows':rows,
                'truncated':count>max_rows}

    def select_row(self,control_handle,row_index,observation_token,open_item=False):
        from pywinauto.controls.common_controls import ListViewWrapper
        observation=self._check(observation_token)
        allowed={row['handle'] for w in observation['tree'] for row in w['controls']}
        if control_handle not in allowed:raise ValueError('Table not in last JADE observation')
        control=self._wrap(control_handle)
        self._actionable(control)
        if control.class_name() not in {'ListView20WndClass','SysListView32'}:raise ValueError('Not a JADE table')
        table=ListViewWrapper(control.handle)
        if not 0<=row_index<table.item_count():raise ValueError('Row index outside table')
        self._consume()
        item=table.get_item(row_index,0)
        selected_text=item.text()
        item.select()
        if open_item:
            # Enter on a selected JADE file-list item reads that file, equivalent to double click.
            table.post_message(0x100,0x0D,0)
            table.post_message(0x101,0x0D,0)
            time.sleep(.4)
        return {'selected_row':row_index,'selected_text':selected_text,'open_dispatched':open_item,'state':self.state()}

    def export_pattern(self,window_handle,observation_token):
        from .clipboard import TemporaryClipboardText
        from . import research as xr
        import win32clipboard
        self._check(observation_token)
        main=self._wrap(window_handle)
        if not main.is_enabled():raise ValueError('Handle the modal JADE dialog before export')
        matches=self.find_menu(window_handle,'复制衍射数据|copy pattern data')['matches']
        if len(matches)!=1:raise ValueError('Cannot uniquely identify JADE Copy Pattern Data menu')
        self._consume()
        with TemporaryClipboardText('JADE_MCP_COPY_PENDING') as temporary:
            try:
                main.post_message(0x111,matches[0]['command_id'],0)
                time.sleep(.6)
                win32clipboard.OpenClipboard()
                try:
                    from ctypes import wintypes
                    import win32process
                    temporary.user.GetClipboardOwner.argtypes=[]
                    temporary.user.GetClipboardOwner.restype=wintypes.HWND
                    owner=temporary.user.GetClipboardOwner()
                    copied_by_jade=bool(owner and win32process.GetWindowThreadProcessId(owner)[1]==main.process_id())
                    if not copied_by_jade:raise RuntimeError('Clipboard was not supplied by JADE; no copied data were exported')
                    text=win32clipboard.GetClipboardData(13)
                    temporary.sequence=temporary.user.GetClipboardSequenceNumber()
                finally:win32clipboard.CloseClipboard()
            finally:
                pass
        if not isinstance(text,str) or text=='JADE_MCP_COPY_PENDING':raise RuntimeError('JADE did not supply copied text data')
        run=xr.new_run('jade_export')
        raw=run/'jade_clipboard.txt'
        raw.write_text(text,encoding='utf-8')
        result={'jade_window_title':main.window_text(),'raw_export':str(raw),'raw_sha256':xr.digest(raw),'output_dir':str(run)}
        try:
            pattern=xr.read_pattern(str(raw))
            import numpy as np
            target=run/'jade_pattern.xy'
            np.savetxt(target,np.column_stack([pattern.x,pattern.y]),fmt='%.10g',header='two_theta_deg intensity')
            result.update(xy_export=str(target),points=len(pattern.x),two_theta_range=pattern.metadata['two_theta_range'])
        except ValueError as exc:
            result['note']='JADE copy format was preserved as text; automatic XY parsing failed: '+str(exc)
        xr.save_json(run/'export.json',result)
        return result
