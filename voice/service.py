import json
import logging

from .models import validate_operations


class VoiceService:
    def __init__(self, store, settings, execute):
        self.store, self.settings, self.execute = store, settings, execute

    def confirm(self, key, user_id, chat_id):
        batch = self.store.get(key, user_id, chat_id)
        if batch is None or batch["status"] != "pending":
            return batch
        # Revalidate the entire package before any write, then atomically claim it.
        try:
            operations = validate_operations(
                json.loads(batch["operations"]), self.settings, stored=True
            )
        except ValueError:
            self.store.finish(key, "failed")
            return self.store.get(key, user_id, chat_id)
        if not self.store.claim(key, user_id, chat_id):
            return self.store.get(key, user_id, chat_id)
        for position, operation in enumerate(operations):
            started = False
            try:
                # Persist BEFORE making the request. A crash leaves an uncertain attempt.
                self.store.mark_attempt(key, position, "executing")
                started = True
                self.execute(operation)
                self.store.mark_attempt(key, position, "completed")
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "voice stage=write batch=%s position=%s error=%s",
                    key,
                    position,
                    type(error).__name__,
                )
                if started:
                    self.store.mark_attempt(key, position, "uncertain")
                self.store.finish(key, "failed")
                break
        else:
            self.store.finish(key, "completed")
        result = self.store.get(key, user_id, chat_id)
        logging.getLogger(__name__).info(
            "voice stage=result batch=%s status=%s", key, result["status"]
        )
        return result
