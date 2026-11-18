"""GUI-affine senders must not die with QRunnable's pool-thread wrapper."""
import gc,threading,time
from unittest.mock import patch
from PySide6.QtCore import QObject,QThreadPool,QEvent,QCoreApplication,Qt
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui


def test_background_signals_are_destroyed_on_gui_after_delivery():
    app=QApplication.instance() or QApplication([])
    gui=threading.get_ident();destruction=[];results=[];failures=[]
    class Owner(QObject):
        _pending_disk_jobs=0;_close_after_jobs=False
        def update_closing_progress(self):pass
    owner=Owner();owner.media_pool=QThreadPool(owner);owner.disk_pool=QThreadPool(owner)
    class Signals(ui.WorkerSignals):
        def __init__(self,parent=None):
            super().__init__(parent)
            assert isinstance(parent,ui.BackgroundCallbacks)
            self.destroyed.connect(lambda:destruction.append(threading.get_ident()==gui),Qt.ConnectionType.DirectConnection)
    def failure():raise RuntimeError('Expected diagnostic failure')
    with patch.object(ui,'WorkerSignals',Signals):
        for index in range(12):
            ui.MainWindow.start_disk_job(owner,failure if index%2 else lambda:42,(),results.append,failures.append)
    until=time.monotonic()+10
    while (owner._pending_disk_jobs or len(destruction)<12) and time.monotonic()<until:
        app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);gc.collect();time.sleep(.001)
    assert results==[42]*6 and failures==['Expected diagnostic failure']*6
    assert destruction==[True]*12
    assert not owner._pending_disk_jobs
    owner.media_pool.waitForDone();owner.disk_pool.waitForDone()
