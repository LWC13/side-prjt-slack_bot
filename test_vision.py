"""
vision 模組的單元測試
執行: .venv/bin/python -m pytest test_vision.py -v
"""

from unittest.mock import patch, MagicMock

import pytest

from vision import analyze_image, download_slack_file, process_slack_image, DEFAULT_PROMPT


# ============================================================
# analyze_image 測試
# ============================================================

class TestAnalyzeImage:
    FAKE_IMAGE = b"fake image bytes"

    def _mock_response(self, text="這是一張貓的照片"):
        """建立模擬的 Gemini response"""
        resp = MagicMock()
        resp.candidates = [MagicMock()]
        resp.text = text
        return resp

    @patch("vision.types.Part.from_bytes")
    @patch("vision.genai.Client")
    def test_default_prompt(self, mock_client_cls, mock_from_bytes):
        """測試使用預設 prompt"""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = self._mock_response()
        mock_client_cls.return_value = mock_client
        mock_from_bytes.return_value = "fake_image_part"

        result, usage_token = analyze_image(self.FAKE_IMAGE, "fake-api-key")

        assert result == "這是一張貓的照片"
        mock_client_cls.assert_called_once_with(api_key="fake-api-key")
        mock_from_bytes.assert_called_once_with(data=self.FAKE_IMAGE, mime_type="image/png")
        mock_client.models.generate_content.assert_called_once_with(
            model="gemini-2.0-flash",
            contents=[DEFAULT_PROMPT, "fake_image_part"],
        )

    @patch("vision.types.Part.from_bytes")
    @patch("vision.genai.Client")
    def test_custom_prompt(self, mock_client_cls, mock_from_bytes):
        """測試自訂 prompt"""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = self._mock_response()
        mock_client_cls.return_value = mock_client
        mock_from_bytes.return_value = "fake_image_part"

        analyze_image(self.FAKE_IMAGE, "fake-api-key", prompt="這是什麼動物？")

        call_args = mock_client.models.generate_content.call_args
        assert call_args.kwargs["contents"][0] == "這是什麼動物？"

    @patch("vision.types.Part.from_bytes")
    @patch("vision.genai.Client")
    def test_custom_mime_type(self, mock_client_cls, mock_from_bytes):
        """測試自訂 MIME type"""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = self._mock_response()
        mock_client_cls.return_value = mock_client
        mock_from_bytes.return_value = "fake_image_part"

        analyze_image(self.FAKE_IMAGE, "fake-api-key", mime_type="image/jpeg")

        mock_from_bytes.assert_called_once_with(data=self.FAKE_IMAGE, mime_type="image/jpeg")

    @patch("vision.types.Part.from_bytes")
    @patch("vision.genai.Client")
    def test_custom_model(self, mock_client_cls, mock_from_bytes):
        """測試自訂模型"""
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = self._mock_response()
        mock_client_cls.return_value = mock_client
        mock_from_bytes.return_value = "fake_image_part"

        analyze_image(self.FAKE_IMAGE, "fake-key", model="gemini-2.0-pro")

        call_args = mock_client.models.generate_content.call_args
        assert call_args.kwargs["model"] == "gemini-2.0-pro"

    @patch("vision.types.Part.from_bytes")
    @patch("vision.genai.Client")
    def test_safety_filter_raises(self, mock_client_cls, mock_from_bytes):
        """測試被安全過濾器阻擋時拋出例外"""
        mock_client = MagicMock()
        resp = MagicMock()
        resp.candidates = []
        resp.prompt_feedback.block_reason = "SAFETY"
        mock_client.models.generate_content.return_value = resp
        mock_client_cls.return_value = mock_client
        mock_from_bytes.return_value = "fake_image_part"

        with pytest.raises(RuntimeError, match="安全過濾器阻擋"):
            analyze_image(self.FAKE_IMAGE, "fake-key")


# ============================================================
# download_slack_file 測試
# ============================================================

class TestDownloadSlackFile:
    @patch("vision.requests.get")
    def test_download_success(self, mock_get):
        """測試成功下載 Slack 檔案"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"image bytes"
        mock_resp.headers = {"Content-Type": "image/jpeg"}
        mock_get.return_value = mock_resp

        data, mime = download_slack_file("https://files.slack.com/file123", "xoxb-token")

        assert data == b"image bytes"
        assert mime == "image/jpeg"
        mock_get.assert_called_once_with(
            "https://files.slack.com/file123",
            headers={"Authorization": "Bearer xoxb-token"},
            timeout=30,
        )

    @patch("vision.requests.get")
    def test_download_failure(self, mock_get):
        """測試下載失敗時拋出例外"""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        with pytest.raises(RuntimeError, match="HTTP 403"):
            download_slack_file("https://files.slack.com/file123", "xoxb-token")


# ============================================================
# process_slack_image 整合測試
# ============================================================

class TestProcessSlackImage:
    @patch("vision.analyze_image")
    @patch("vision.download_slack_file")
    def test_full_pipeline(self, mock_download, mock_analyze):
        """測試完整流程：下載 → 分析"""
        mock_download.return_value = (b"fake image", "image/png")
        mock_analyze.return_value = "這是一張貓的照片"

        result = process_slack_image("https://files.slack.com/f1", "slack-token", "gemini-key", prompt="這是什麼？")

        assert result == "這是一張貓的照片"
        mock_download.assert_called_once_with("https://files.slack.com/f1", "slack-token")
        mock_analyze.assert_called_once_with(b"fake image", "gemini-key", "image/png", "這是什麼？")
