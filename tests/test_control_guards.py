import time

import pytest

from jade_mcp.control import JadeController


def test_reject_foreign_window():
    import win32gui
    controller=JadeController()
    with pytest.raises(ValueError): controller._wrap(win32gui.GetDesktopWindow())


def test_stale_and_single_use_tokens(monkeypatch):
    controller=JadeController()
    monkeypatch.setattr(controller,'_tree',lambda:[])
    token=controller.state()['observation_token']
    controller._check(token)
    controller._consume()
    with pytest.raises(ValueError):controller._check(token)
    token=controller.state()['observation_token']
    controller._snapshot['created']=time.monotonic()-91
    with pytest.raises(ValueError):controller._check(token)


def test_ui_change_invalidates_token(monkeypatch):
    controller=JadeController()
    monkeypatch.setattr(controller,'_tree',lambda:[])
    token=controller.state()['observation_token']
    monkeypatch.setattr(controller,'_tree',lambda:[{'handle':123,'text':'new modal'}])
    with pytest.raises(ValueError,match='changed'):controller._check(token)


def test_semantic_button_preserves_mouse_button_argument(monkeypatch):
    import win32gui
    import jade_mcp.control as module
    monkeypatch.setattr(win32gui,'GetClientRect',lambda h:(0,0,80,24))
    monkeypatch.setattr(win32gui,'GetAncestor',lambda h,flag:456)
    class Root:
        def set_focus(self):pass
    monkeypatch.setattr(module,'HwndWrapper',lambda h:Root())
    class Control:
        handle=123
        def click(self,**kwargs):self.clicked=kwargs
    control=Control()
    JadeController._post_button(control)
    assert control.clicked=={'button':'left','coords':(40,12)}
    JadeController._post_button(control,right=True)
    assert control.clicked['button']=='right'


def test_enabled_modal_uses_root_not_disabled_owner(monkeypatch):
    import win32gui
    import jade_mcp.control as module
    seen=[]
    monkeypatch.setattr(win32gui,'GetAncestor',lambda h,flag:seen.append(flag) or 456)
    class Control:
        handle=123
        def is_enabled(self):return True
    monkeypatch.setattr(module,'HwndWrapper',lambda h:Control())
    JadeController._actionable(Control())
    assert seen==[2]  # GA_ROOT, not GA_ROOTOWNER


def test_click_translates_window_frame_to_screen_and_rejects_occlusion(monkeypatch):
    import win32gui
    import win32process
    from pywinauto import mouse
    from pywinauto.win32structures import RECT
    controller=JadeController()
    class Window:
        handle=123
        def rectangle(self):return RECT(400,300,1000,800)
        def set_focus(self):pass
        def process_id(self):return 42
    monkeypatch.setattr(controller,'_check',lambda token:None)
    monkeypatch.setattr(controller,'_wrap',lambda h:Window())
    monkeypatch.setattr(controller,'_actionable',lambda w:None)
    monkeypatch.setattr(controller,'state',lambda:{'ok':True})
    monkeypatch.setattr(win32gui,'WindowFromPoint',lambda p:123)
    monkeypatch.setattr(win32process,'GetWindowThreadProcessId',lambda h:(1,42))
    clicked=[]
    monkeypatch.setattr(mouse,'click',lambda **kw:clicked.append(kw))
    controller.coordinate_click(123,100,70,'token')
    assert clicked==[{'coords':(500,370)}]
    with pytest.raises(ValueError,match='outside'):
        controller.coordinate_click(123,600,70,'token')
    monkeypatch.setattr(win32process,'GetWindowThreadProcessId',lambda h:(1,99))
    with pytest.raises(RuntimeError,match='occluded'):
        controller.coordinate_click(123,100,70,'token')
    assert len(clicked)==1


def test_connect_restores_only_minimized_jade_main(tmp_path,monkeypatch):
    import jade_mcp.control as module
    controller=JadeController()
    controller.exe=tmp_path/'jade9c.exe'
    controller.exe.touch()
    monkeypatch.setattr(controller,'processes',lambda:[{'pid':42}])
    monkeypatch.setattr(controller,'state',lambda:{'ok':True})
    class Window:
        def __init__(self,pid,title):self.pid=pid;self.title=title;self.restored=False
        def process_id(self):return self.pid
        def window_text(self):return self.title
        def is_minimized(self):return True
        def restore(self):self.restored=True
    windows=[Window(42,'Jade 9 [test]'),Window(43,'Jade other app'),Window(42,'Analysis dialog')]
    class Desktop:
        def windows(self,**kw):return windows
    monkeypatch.setattr(module,'Desktop',lambda **kw:Desktop())
    assert controller.launch()=={'ok':True}
    assert [w.restored for w in windows]==[True,False,False]


def test_launch_uses_desktop_when_no_explorer_folder_open(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import win32com.client
    calls=[]
    launcher=SimpleNamespace(ShellExecute=lambda *args:calls.append(args))
    desktop=SimpleNamespace(Document=SimpleNamespace(Application=launcher))
    class Windows:
        def FindWindowSW(self,*args):
            assert args==(0,0,8,0,1)
            return desktop
        def __iter__(self):return iter([])
    shell=SimpleNamespace(Windows=lambda:Windows())
    monkeypatch.setattr(win32com.client,'Dispatch',lambda name:shell)
    exe=tmp_path/'jade9c.exe'
    JadeController._launch_from_shell(exe)
    assert calls==[(str(exe),'',str(tmp_path),'open',1)]
