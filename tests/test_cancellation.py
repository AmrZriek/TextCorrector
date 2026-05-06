import pytest
import threading
import time
from text_corrector import ModelManager

class MockConfig:
    def get(self, key, default=None): return default

def test_rewrite_chunk_cancellation(monkeypatch):
    mgr = ModelManager(MockConfig())
    mgr._chat_url = lambda: "http://fake"
    
    cancel_event = threading.Event()
    
    # Mock requests.Session.post to simulate a long-running request that gets cancelled
    class MockSession:
        def __init__(self):
            pass
        def post(self, url, json, timeout):
            # Wait a bit, checking if cancelled
            start = time.time()
            while time.time() - start < 1.0:
                if cancel_event.is_set():
                    import requests
                    raise requests.exceptions.ConnectionError("Cancelled via session.close()")
                time.sleep(0.01)
            class R:
                ok = True
                status_code = 200
                def json(self): return {"choices": [{"message": {"content": "<<<START>>>test<<<END>>>"}}]}
                def raise_for_status(self): pass
            return R()
        def close(self):
            cancel_event.set() # Simulate the close triggering the connection error
            
    monkeypatch.setattr("requests.Session", MockSession)
    
    # In a separate thread, cancel after 0.1s
    def cancel_later():
        time.sleep(0.1)
        # Note: the real implementation will cancel via the session watcher
        cancel_event.set()
        
    threading.Thread(target=cancel_later).start()
    
    # This should return None because it was cancelled (ConnectionError caught)
    res = mgr._rewrite_sentence_chunk("test", None, 1, 1, "smart_fix", cancel_event=cancel_event)
    assert res is None
