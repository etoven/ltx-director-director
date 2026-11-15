"""Queue editing through the native panel and its dependency scheduler."""
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from ltx_prompt_director.render_widgets import RenderQueuePanel, QueueJobDelegate
from test_render_queue import APP, setup, submit, tick


def test_sweep_preserves_completed_dependencies_and_delete_cancels_dependents():
    queue, runs = setup()
    first = submit(queue, 'first')
    second = submit(queue, 'second', dependencies=(first.id,))
    third = submit(queue, 'third', dependencies=(second.id,))
    runs[0][2]({'path': 'keep-first.mp4'})
    queue.sweep()
    tick()
    assert first not in queue.jobs and second is queue.active
    queue.delete_job(second.id)
    assert second.state == 'Cancelling' and third not in queue.jobs
    runs[1][2]({'path': 'discarded-result.mp4'})
    tick()
    assert queue.jobs == [] and queue.active is None
    assert first.output_path == 'keep-first.mp4'


def test_drag_order_keeps_dependencies_and_active_job_in_place():
    queue, runs = setup()
    active = submit(queue, 'active')
    first = submit(queue, 'first', group_id='group', group_title='Generation')
    second = submit(queue, 'second', group_id='group', group_title='Generation', dependencies=(first.id,))
    independent = submit(queue, 'independent')
    assert not queue.move_job(second.id, first.id)
    assert not queue.move_job('group', active.id)
    assert queue.move_job(independent.id, 'group')
    assert queue.jobs == [active, independent, first, second]
    assert queue.move_job('group', independent.id)
    assert queue.jobs == [active, first, second, independent]
    assert not queue.move_job(first.id, None)


def test_native_panel_keeps_scroll_and_selection_on_updates_and_deletes_any_state():
    queue, runs = setup()
    queue.set_paused(True)
    jobs = [submit(queue, str(index)) for index in range(35)]
    panel = RenderQueuePanel(queue)
    panel.resize(440, 650)
    panel.show()
    tick()
    panel.list.setCurrentRow(17)
    panel.list.scrollToItem(panel.list.item(15))
    tick()
    bar = panel.list.verticalScrollBar()
    position = bar.value()
    selected = panel.selected_job().id
    item = panel.list.currentItem()
    for step in range(5):
        jobs[0].percent = step * 10
        queue.changed.emit()
        tick()
        assert bar.value() == position
        assert panel.selected_job().id == selected
        assert panel.list.currentItem() is item
    submit(queue, 'new arrival')
    tick()
    assert bar.value() == position and panel.selected_job().id == selected
    queue.set_paused(False)
    tick()
    assert bar.value() == position and panel.selected_job().id == selected
    panel.list.scrollToTop()
    tick()
    delete = QueueJobDelegate.delete_button(panel.list.visualItemRect(panel.list.item(0)))
    QTest.mouseClick(panel.list.viewport(), Qt.MouseButton.LeftButton, pos=delete.center())
    assert jobs[0].state == 'Cancelling'
    runs[0][2]('ignored')
    tick()
    assert jobs[0] not in queue.jobs
    queue.set_paused(True)
    runs[1][3]('failed')
    queue.cancel_job(jobs[2].id)
    panel.sweep.click()
    assert jobs[1] not in queue.jobs and jobs[2] not in queue.jobs
    queue.shutdown()
    panel.close()


def test_native_drop_reorders_waiting_groups_and_rejects_dependency_inversion():
    from PySide6.QtCore import QMimeData, QPointF
    from PySide6.QtGui import QDropEvent
    queue, runs = setup()
    queue.set_paused(True)
    first = submit(queue, 'first', group_id='generation', group_title='Generation')
    second = submit(queue, 'second', group_id='generation', group_title='Generation',dependencies=(first.id,))
    other = submit(queue, 'other')
    panel = RenderQueuePanel(queue)
    panel.resize(440,650)
    panel.show()
    tick()

    class Drop(QDropEvent):
        def source(self):
            return panel.list

    def drop(job_id, target_row):
        data = QMimeData()
        data.setData('application/x-director-render-job', job_id.encode())
        target = panel.list.visualItemRect(panel.list.item(target_row))
        event = Drop(QPointF(target.left()+40,target.top()+3), Qt.DropAction.MoveAction,
                     data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        panel.list.dropEvent(event)
        tick()

    drop(other.id,0)
    assert queue.jobs == [other,first,second]
    drop(second.id,2)
    assert queue.jobs == [other,first,second]
    assert panel.file_status.text()
    queue.shutdown()
    panel.close()
