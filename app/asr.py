# -*- coding: utf-8 -*-
"""MiMo-V2.5-ASR 客户端：base64 上传、重试、限流、错误分类。

接口（已实测可用）：
    POST {base_url}/chat/completions
    body: {model, messages:[{role:user, content:[{type:input_audio,
           input_audio:{data:"data:audio/mpeg;base64,..."}}]}],
           asr_options:{language:"auto"}}
"""
from __future__ import annotations

import base64
import os
import threading
import time

import requests

MAX_B64_BYTES = 9.5 * 1024 * 1024     # 保守上限，官方说 10MB


class AsrError(Exception):
    """带可重试标记的接口异常。"""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class RateLimiter:
    """简单的匀速令牌桶，避免瞬时打满。"""

    def __init__(self, rate_per_sec: float = 4.0):
        self.interval = 1.0 / max(0.1, rate_per_sec)
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            time.sleep(wait)


_local = threading.local()


def _session() -> requests.Session:
    sess = getattr(_local, "sess", None)
    if sess is None:
        sess = requests.Session()
        _local.sess = sess
    return sess


class AsrClient:
    def __init__(self, api_key: str, base_url: str, model: str = "mimo-v2.5-asr",
                 timeout: int = 180, max_retries: int = 3, rate: float = 4.0):
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.limiter = RateLimiter(rate)

    # ------------------------------------------------------------ 内部
    def _endpoint(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def _encode(self, audio_path: str) -> str:
        size = os.path.getsize(audio_path)
        b64_len = size * 4 / 3 + 128            # base64 膨胀后的大致长度
        if b64_len > MAX_B64_BYTES:
            raise AsrError(
                f"音频体积过大，base64 后约 {b64_len/1024/1024:.1f} MB，超过 9.5 MB 上限。"
                f"请把切片长度调小。", retryable=False)
        with open(audio_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    # ------------------------------------------------------------ 对外
    def transcribe(self, audio_path: str, language: str = "auto") -> str:
        """转写单段音频，返回文本（可能为空串表示静音）。失败抛 AsrError。"""
        if not self.api_key:
            raise AsrError("未配置 API Key", retryable=False)

        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {"data": f"data:audio/mpeg;base64,{self._encode(audio_path)}"},
                }],
            }],
            "asr_options": {"language": language},
        }

        last: AsrError | None = None
        for attempt in range(1, self.max_retries + 1):
            self.limiter.acquire()
            try:
                resp = _session().post(self._endpoint(), json=payload,
                                       headers=self._headers(), timeout=self.timeout)
            except requests.Timeout as exc:
                last = AsrError(f"请求超时（{self.timeout}s）", retryable=True)
            except requests.RequestException as exc:
                last = AsrError(f"网络错误: {exc}", retryable=True)
            else:
                status = resp.status_code
                if status == 200:
                    try:
                        data = resp.json()
                        return (data["choices"][0]["message"]["content"] or "").strip()
                    except (KeyError, IndexError, ValueError) as exc:
                        last = AsrError(f"响应格式异常: {exc}", status=200, retryable=True)
                elif status in (401, 403):
                    raise AsrError(f"鉴权失败（{status}），请检查 API Key", status=status)
                elif status == 400:
                    raise AsrError(f"请求被拒（400）：{resp.text[:300]}", status=status)
                elif status == 429:
                    last = AsrError("触发限流（429）", status=status, retryable=True)
                    time.sleep(5 * attempt)
                    continue
                elif 500 <= status < 600:
                    last = AsrError(f"服务端错误（{status}）", status=status, retryable=True)
                else:
                    raise AsrError(f"未知状态码 {status}：{resp.text[:300]}", status=status)

            if attempt < self.max_retries:
                time.sleep(2 ** attempt)        # 2 / 4 / 8 秒

        raise last or AsrError("转写失败", retryable=True)

    # ------------------------------------------------------------ 自检
    def test_connection(self, tmp_dir: str) -> tuple[bool, str]:
        """用一段 2 秒静音验证 key 和端点是否可用。返回 (是否通过, 说明)。"""
        from . import audio

        if not self.api_key:
            return False, "还没填 API Key"
        os.makedirs(tmp_dir, exist_ok=True)
        probe = os.path.join(tmp_dir, "_conn_test.mp3")
        try:
            audio.make_silence_mp3(probe, seconds=1.5)
        except Exception as exc:  # noqa: BLE001
            return False, f"生成测试音频失败: {exc}"

        try:
            t0 = time.time()
            self.transcribe(probe, language="auto")
            return True, f"连接正常（{time.time()-t0:.1f}s）"
        except AsrError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001
            return False, f"未知错误: {exc}"
        finally:
            try:
                os.remove(probe)
            except OSError:
                pass
