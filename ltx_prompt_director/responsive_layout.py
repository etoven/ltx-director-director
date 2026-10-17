"""Compact native control rows that wrap within their panel's available width."""
from PySide6.QtCore import QRect,QSize,Qt
from PySide6.QtWidgets import QLayout, QWidgetItem


class ControlRow(QLayout):
    def __init__(self,parent=None):
        super().__init__(parent);self._items=[];self.setSpacing(6)

    def addItem(self,item):self._items.append(item)
    def insertWidget(self, index, widget):
        """Insert a widget at a precise position in this compact flow row."""
        if widget is None:
            return
        self.addChildWidget(widget)
        position = max(0, min(int(index), len(self._items)))
        self._items.insert(position, QWidgetItem(widget))
        self.invalidate()

    def count(self):return len(self._items)
    def itemAt(self,index):return self._items[index] if 0<=index<len(self._items) else None
    def takeAt(self,index):return self._items.pop(index) if 0<=index<len(self._items) else None
    def addStretch(self,*_):pass
    def expandingDirections(self):return Qt.Orientation(0)
    def hasHeightForWidth(self):return True
    def heightForWidth(self,width):return self._arrange(QRect(0,0,width,0),False)
    def sizeHint(self):return self.minimumSize()
    def minimumSize(self):
        size=QSize()
        for item in self._items:
            if not item.isEmpty():size=size.expandedTo(item.minimumSize())
        left,top,right,bottom=self.getContentsMargins()
        return size+QSize(left+right,top+bottom)

    def setGeometry(self,rect):
        super().setGeometry(rect);self._arrange(rect,True)

    def _arrange(self,rect,apply):
        left,top,right,bottom=self.getContentsMargins()
        area=rect.adjusted(left,top,-right,-bottom)
        x=area.left();y=area.top();height=0;gap=max(0,self.spacing())
        for item in self._items:
            if item.isEmpty():continue
            size=item.sizeHint().expandedTo(item.minimumSize())
            if item.widget() is not None:
                size=size.expandedTo(item.widget().sizeHint())
            size.setWidth(min(size.width(),max(1,area.width())))
            if x>area.left() and x+size.width()>area.right()+1:
                x=area.left();y+=height+gap;height=0
            if apply:item.setGeometry(QRect(x,y,size.width(),size.height()))
            x+=size.width()+gap;height=max(height,size.height())
        return y+height-rect.y()+bottom
