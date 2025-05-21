import asyncio
from telegram import Update, Message
from telegram.ext import ContextTypes

class Task:
    """
    Generic asynchronous bot task.
    Subclasses must implement `_process()` and may override `cleanup()`.
    """

    def __init__(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        status_msg: Message,
    ) -> None:
        """
        :param update: incoming Update
        :param context: callback Context
        :param status_msg: message to edit with status updates
        """
        self.update = update
        self.context = context
        self.status_msg = status_msg
        self.created_at = asyncio.get_event_loop().time()

    async def run(self) -> None:
        """
        Wraps `_process()` with error handling and ensures `cleanup()` runs.
        """
        try:
            await self._process()
        except Exception as e:
            # subclasses may handle their own errors, or you can log here
            raise
        finally:
            await self.cleanup()

    async def _process(self) -> None:
        """
        Main work of the task. Must be implemented by subclasses.
        """
        raise NotImplementedError

    async def cleanup(self) -> None:
        """
        Called after `_process()` or on error. Subclasses can override.
        """
        pass