"""Audition the assembled sequence while seeking, preserving playback intent."""
from PySide6.QtCore import QObject, QTimer
from PySide6.QtMultimedia import QMediaPlayer


class AudioScrubber(QObject):
    def __init__(self,player,parent=None):
        super().__init__(parent)
        self.player=player;self.active=False;self.dragging=False;self.resume=False;self.target=0
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(140)
        self.timer.timeout.connect(self.finish)

    def begin(self):
        if not self.active:
            self.resume=self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        self.active=True;self.dragging=True;self.timer.stop()

    def seek(self,position):
        if not self.active:
            self.resume=self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        self.active=True;self.target=position
        self.player.setPosition(position)
        # The player already has the sequence's mixed audio and video. Brief
        # playback provides an audible sample even when the transport is paused.
        self.player.play()
        self.timer.start()

    def end(self):
        self.dragging=False
        if self.active:self.timer.start()

    def finish(self):
        if not self.active:return
        if not self.resume:
            self.player.pause();self.player.setPosition(self.target)
        if not self.dragging:self.active=False

    def cancel(self):
        self.timer.stop();self.active=False;self.dragging=False;self.resume=False
