import logging

from PyQt6.QtCore import QObject, QThread, QCoreApplication, pyqtSignal

logger = logging.getLogger(__name__)


class LatchSignal:
    """Signal wrapper that replays finished/error/completed if already emitted before connect."""
    def __init__(self, bound_signal, worker, signal_name):
        self._bound = bound_signal
        self._worker = worker
        self._name = signal_name

    def connect(self, slot):
        res = self._bound.connect(slot)
        if self._name == "finished" and getattr(self._worker, "_is_finished", False):
            val = self._worker._result
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: slot(val))
        elif self._name == "error" and getattr(self._worker, "_is_error", False):
            val = self._worker._error_val
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: slot(val))
        elif self._name == "completed" and getattr(self._worker, "_is_completed", False):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, slot)
        return res

    def disconnect(self, *args, **kwargs):
        return self._bound.disconnect(*args, **kwargs)

    def emit(self, *args, **kwargs):
        return self._bound.emit(*args, **kwargs)


class Worker(QObject):
    _finished = pyqtSignal(object)
    _error = pyqtSignal(tuple)
    _completed = pyqtSignal()

    def __init__(self, target_func, *args, **kwargs):
        super().__init__()
        self.target_func = target_func
        self.args = args
        self.kwargs = kwargs
        self._is_finished = False
        self._is_error = False
        self._is_completed = False
        self._result = None
        self._error_val = None

        self.finished = LatchSignal(self._finished, self, "finished")
        self.error = LatchSignal(self._error, self, "error")
        self.completed = LatchSignal(self._completed, self, "completed")

    def run(self):
        func_name = self.target_func.__name__
        logger.debug(f"Worker starting execution of function: '{func_name}'")
        try:
            result = self.target_func(*self.args, **self.kwargs)
            self._result = result
            self._is_finished = True
            self._finished.emit(result)
            logger.debug(f"Worker finished function '{func_name}' successfully.")
        except BaseException as e:
            # Catch BaseException (not just Exception) so that gevent.timeout.Timeout
            # and similar non-Exception subclasses don't silently kill the thread
            # without emitting completed(), which would deadlock the job queue.
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise
            logger.error(
                f"An error occurred in worker function '{func_name}': {e}",
                exc_info=True,
            )
            import traceback

            self._error_val = (type(e), e, traceback.format_exc())
            self._is_error = True
            self._error.emit(self._error_val)
        finally:
            self._is_completed = True
            self._completed.emit()
            logger.debug(f"Worker completed task for function '{func_name}'.")


class TaskRunner(QObject):
    _active_runners = []
    _shutdown_hooked = False
    cleanup_complete = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = None
        self.worker = None
        self.destroyed.connect(self._on_destroyed)

    def run(self, target_func, *args, on_finished=None, on_error=None, on_completed=None, **kwargs):
        self._ensure_shutdown_hook()

        self._thread = QThread(self)
        self.worker = Worker(target_func, *args, **kwargs)
        self.worker.moveToThread(self._thread)

        if on_finished is not None:
            self.worker.finished.connect(on_finished)
        if on_error is not None:
            self.worker.error.connect(on_error)
        if on_completed is not None:
            self.worker.completed.connect(on_completed)

        self._thread.started.connect(self.worker.run)
        self.worker.completed.connect(self._thread.quit)
        self._thread.finished.connect(self.worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.finished.connect(self._cleanup)

        self._thread.finished.connect(self.cleanup_complete)

        self._thread.start()
        logger.info(
            f"Task for function '{target_func.__name__}' has been started in a new thread."
        )

        TaskRunner._active_runners.append(self)

        return self.worker

    def stop(self, wait_ms=2000, terminate_on_timeout=True):
        """Stop the current task and clean up resources safely."""
        self._request_task_stop()
        if self._thread is not None and self._thread.isRunning():
            try:
                self._thread.quit()
                if wait_ms is None:
                    wait_ms = 2000
                if wait_ms > 0 and not self._thread.wait(wait_ms):
                    logger.warning("Thread did not finish in time during stop()")
                    if terminate_on_timeout:
                        self._thread.terminate()
                        self._thread.wait()
            except RuntimeError:
                # Thread may have already been deleted by Qt
                logger.debug("Thread was already deleted during stop()")
        self._thread = None
        self.worker = None

    def _cleanup(self):
        if self.worker:
            func_name = self.worker.target_func.__name__
            logger.debug(f"Cleaning up TaskRunner instance for '{func_name}'.")
        else:
            logger.debug("Cleaning up TaskRunner instance for a completed task.")

        if self in TaskRunner._active_runners:
            TaskRunner._active_runners.remove(self)

        # Nullify references to prevent dangling references
        self._thread = None
        self.worker = None

    def _request_task_stop(self):
        """Attempt to call stop() on the bound task object if available."""
        try:
            if not self.worker:
                return
            bound_self = getattr(self.worker.target_func, "__self__", None)
            if bound_self and hasattr(bound_self, "stop"):
                bound_self.stop()
        except RuntimeError as e:
            logger.debug(f"Failed to request task stop: {e}")

    def _on_destroyed(self, _obj=None):
        try:
            self.stop(wait_ms=0, terminate_on_timeout=True)
        except RuntimeError:
            pass

    @classmethod
    def stop_all_active(cls):
        runners = list(cls._active_runners)
        for runner in runners:
            try:
                runner.stop(wait_ms=0, terminate_on_timeout=True)
            except RuntimeError as e:
                logger.debug(f"Failed to stop TaskRunner during shutdown: {e}")

    @classmethod
    def _ensure_shutdown_hook(cls):
        if cls._shutdown_hooked:
            return

        app = QCoreApplication.instance()
        if app is None:
            return

        try:
            app.aboutToQuit.connect(cls.stop_all_active)
            cls._shutdown_hooked = True
        except RuntimeError as e:
            logger.debug(f"Failed to register shutdown hook: {e}")
