# coding: utf-8
"""Collaboration mode interface.

In collab mode, the ring records audio (long-press to record, release to send).
The audio is received via BLE, transcribed via ASR API, then sent to an AI
inference model.  The streaming response is displayed in a chat-like view.
"""
import base64
import json

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy

from qfluentwidgets import (ScrollArea, TitleLabel, BodyLabel,
                            StrongBodyLabel, CaptionLabel, SubtitleLabel,
                            SimpleCardWidget, TogglePushButton,
                            InfoBar, InfoBarPosition,
                            IndeterminateProgressBar)

from ..common.style_sheet import StyleSheet
from ..common.signal_bus import signalBus
from ..common.config import cfg


def _get_shared_client():
    from . import connect_interface
    return connect_interface.shared_client


def _get_loop_thread():
    from . import connect_interface
    return connect_interface.async_loop_thread


# ── Worker Threads ──────────────────────────────────────────────

class AudioReceiveThread(QThread):
    """Receive auto-reported audio from ring after recording."""
    audioReceived = pyqtSignal(int, bytes)  # file_index, raw_audio
    error = pyqtSignal(str)

    def __init__(self, client, loop_thread, parent=None):
        super().__init__(parent)
        self.client = client
        self.loop_thread = loop_thread
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        try:
            from ..sdk import ring_sound as sdk
            # Use 15s timeout in a polling loop so the thread stays
            # responsive and never holds the shared event loop hostage.
            while not self._stop_flag:
                try:
                    file_index, raw_audio = self.loop_thread.run_coro(
                        sdk.receive_auto_audio_file(
                            self.client, timeout_s=15.0),
                        timeout=20,
                    )
                    self.audioReceived.emit(file_index, raw_audio)
                    return
                except Exception:
                    # Timeout or transient error – retry
                    continue
        except Exception as e:
            self.error.emit(str(e))


class AsrWorkerThread(QThread):
    """Send audio to StepFun ASR API and return transcribed text."""
    transcriptionDone = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, raw_audio: bytes, api_key: str, parent=None):
        super().__init__(parent)
        self.raw_audio = raw_audio
        self.api_key = api_key

    def run(self):
        try:
            from ..sdk import ring_sound as sdk
            # Decode Speex to PCM
            result = sdk.decode_speex_to_pcm(self.raw_audio)
            pcm_bytes = result.pcm_bytes

            # Base64 encode
            audio_b64 = base64.b64encode(pcm_bytes).decode('utf-8')

            # Call ASR API (SSE streaming)
            import requests
            url = 'https://api.stepfun.com/step_plan/v1/audio/asr/sse'
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream',
                'Authorization': f'Bearer {self.api_key}',
            }
            payload = {
                'audio': {
                    'data': audio_b64,
                    'input': {
                        'transcription': {
                            'model': 'stepaudio-2.5-asr',
                            'language': 'zh',
                            'enable_itn': True,
                        },
                        'format': {
                            'type': 'pcm',
                            'codec': 'pcm_s16le',
                            'rate': 16000,
                            'bits': 16,
                            'channel': 1,
                        },
                    },
                },
            }

            resp = requests.post(url, json=payload, headers=headers,
                                 stream=True, timeout=60)
            resp.raise_for_status()

            # Parse SSE response - collect all text
            full_text = []
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith('data: '):
                    data_str = line[6:]
                    if data_str.strip() == '[DONE]':
                        break
                    try:
                        data = json.loads(data_str)
                        # Try common response structures
                        if isinstance(data, dict):
                            text = (data.get('text', '')
                                    or data.get('transcript', '')
                                    or data.get('content', ''))
                            if not text and 'choices' in data:
                                for choice in data['choices']:
                                    text += choice.get('text', '')
                            if text:
                                full_text.append(text)
                    except json.JSONDecodeError:
                        # Might be plain text
                        full_text.append(data_str)

            result_text = ''.join(full_text).strip()
            if not result_text:
                self.error.emit('语音识别未返回文本结果')
            else:
                self.transcriptionDone.emit(result_text)

        except Exception as e:
            self.error.emit(str(e))


class ChatWorkerThread(QThread):
    """Stream chat completion from StepFun API."""
    textDelta = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, user_text: str, api_key: str, model: str,
                 system_prompt: str, history: list, parent=None):
        super().__init__(parent)
        self.user_text = user_text
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.history = history  # list of (role, content) tuples

    def run(self):
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=self.api_key,
                base_url='https://api.stepfun.com/step_plan/v1',
            )

            messages = [{'role': 'system', 'content': self.system_prompt}]
            # Add history (last 10 turns max)
            for role, content in self.history[-10:]:
                messages.append({'role': role, 'content': content})
            messages.append({'role': 'user', 'content': self.user_text})

            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
            )

            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    self.textDelta.emit(chunk.choices[0].delta.content)

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))


# ── Chat Bubble Widgets ─────────────────────────────────────────

class ChatBubble(QWidget):
    """A single chat message bubble."""

    def __init__(self, role: str, text: str = '', parent=None):
        super().__init__(parent)
        self.setObjectName('chatBubbleUser' if role == 'user'
                           else 'chatBubbleAssistant')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumWidth(200)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 8, 12, 8)
        self._layout.setSpacing(4)

        # Role label
        role_text = '语音转写' if role == 'user' else 'AI 助手'
        self.roleLabel = CaptionLabel(role_text, self)
        self.roleLabel.setTextColor(
            QColor(80, 80, 80), QColor(180, 180, 180))

        # Content label
        self.contentLabel = BodyLabel(text, self)
        self.contentLabel.setWordWrap(True)
        self.contentLabel.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        self.contentLabel.setObjectName('bubbleContent')

        self._layout.addWidget(self.roleLabel)
        self._layout.addWidget(self.contentLabel)

        if role == 'user':
            self.roleLabel.setTextColor(
                QColor(0, 120, 60), QColor(77, 203, 102))
        else:
            self.roleLabel.setTextColor(
                QColor(0, 90, 180), QColor(100, 160, 255))

    def setText(self, text: str):
        self.contentLabel.setText(text)

    def appendText(self, text: str):
        current = self.contentLabel.text()
        self.contentLabel.setText(current + text)


class ThinkingWidget(QWidget):
    """Widget showing a thinking/loading indicator."""

    def __init__(self, text='正在思考...', parent=None):
        super().__init__(parent)
        self.setObjectName('chatBubbleAssistant')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self.progress = IndeterminateProgressBar(self)
        self.progress.setFixedSize(16, 16)
        self.progress.start()

        self.label = BodyLabel(text, self)
        self.label.setTextColor(QColor(120, 120, 120), QColor(160, 160, 160))

        layout.addWidget(self.progress)
        layout.addWidget(self.label)
        layout.addStretch(1)


# ── Main Interface ──────────────────────────────────────────────

class CollabInterface(ScrollArea):
    """Collaboration mode interface."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self.view = QWidget(self)
        self.vBoxLayout = QVBoxLayout(self.view)

        # State
        self._active = False
        self._chatHistory = []  # list of (role, content) tuples
        self._currentBubble = None
        self._thinkingWidget = None
        self._workerThreads = []

        # Title
        self.titleLabel = TitleLabel('协同模式', self.view)
        self.subtitleLabel = BodyLabel(
            '长按戒指录音，松开后自动转写并交给 AI 回答', self.view)

        # Status card
        self.statusCard = SimpleCardWidget(self.view)
        self.statusCard.setBorderRadius(12)
        self.statusCard.setFixedHeight(80)

        self.statusLabel = StrongBodyLabel('协同模式未开启', self.statusCard)
        self.statusLabel.setObjectName('collabStatusLabel')
        self.statusLabel.setProperty('active', False)
        self.connectionHint = CaptionLabel('请先连接戒指设备', self.statusCard)
        self.connectionHint.setTextColor(
            QColor(96, 96, 96), QColor(180, 180, 180))

        self.toggleBtn = TogglePushButton('开启协同模式', self.statusCard)
        self.toggleBtn.setFixedWidth(160)
        self.toggleBtn.setEnabled(False)

        self._buildStatusCard()

        # Chat area
        self.chatSection = SubtitleLabel('对话记录', self.view)
        self.chatContainer = QVBoxLayout()
        self.chatContainer.setSpacing(12)
        self.chatContainer.setAlignment(Qt.AlignTop)

        self.emptyHint = CaptionLabel(
            '开启协同模式后，长按戒指录音即可开始对话', self.view)
        self.emptyHint.setAlignment(Qt.AlignCenter)
        self.emptyHint.setTextColor(
            QColor(150, 150, 150), QColor(120, 120, 120))
        self.chatContainer.addWidget(self.emptyHint)

        self.__initWidget()

    def _buildStatusCard(self):
        cardLayout = QHBoxLayout(self.statusCard)
        cardLayout.setContentsMargins(20, 16, 20, 16)
        cardLayout.setSpacing(16)

        textLayout = QVBoxLayout()
        textLayout.setSpacing(4)
        textLayout.addWidget(self.statusLabel)
        textLayout.addWidget(self.connectionHint)
        textLayout.addStretch(1)
        cardLayout.addLayout(textLayout)
        cardLayout.addStretch(1)
        cardLayout.addWidget(self.toggleBtn, 0, Qt.AlignVCenter)

    def __initWidget(self):
        self.setObjectName('collabInterface')
        self.view.setObjectName('view')

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        StyleSheet.COLLAB_INTERFACE.apply(self)

        self.vBoxLayout.setContentsMargins(36, 24, 36, 36)
        self.vBoxLayout.setSpacing(20)
        self.vBoxLayout.setAlignment(Qt.AlignTop)

        self.vBoxLayout.addWidget(self.titleLabel)
        self.vBoxLayout.addWidget(self.subtitleLabel)
        self.vBoxLayout.addSpacing(4)
        self.vBoxLayout.addWidget(self.statusCard)
        self.vBoxLayout.addSpacing(8)
        self.vBoxLayout.addWidget(self.chatSection)
        self.vBoxLayout.addLayout(self.chatContainer)
        self.vBoxLayout.addStretch(1)

        # Signals
        self.toggleBtn.toggled.connect(self.__onToggleMode)
        signalBus.deviceConnected.connect(self.__onDeviceConnected)
        signalBus.deviceDisconnected.connect(self.__onDeviceDisconnected)
        signalBus.modeStarted.connect(self.__onOtherModeStarted)

        if _get_shared_client() is not None:
            self.__onDeviceConnected('', '')

    # ── Mode toggle ───────────────────────────────────────────────

    def __onToggleMode(self, checked: bool):
        if checked:
            self.__startCollabMode()
        else:
            self.__stopCollabMode()

    def __startCollabMode(self):
        client = _get_shared_client()
        if client is None:
            self.toggleBtn.setChecked(False)
            InfoBar.warning('未连接设备', '请先在「连接戒指」页面连接戒指',
                            parent=self.window(), duration=3000,
                            position=InfoBarPosition.TOP_RIGHT)
            return

        api_key = cfg.get(cfg.stepFunApiKey)
        if not api_key:
            self.toggleBtn.setChecked(False)
            InfoBar.warning('未配置 API Key',
                            '请先在「设置」页面配置 StepFun API Key',
                            parent=self.window(), duration=4000,
                            position=InfoBarPosition.TOP_RIGHT)
            return

        self._active = True
        signalBus.modeStarted.emit('collab')
        self.statusLabel.setText('协同模式已开启 - 等待录音')
        self.statusLabel.setProperty('active', True)
        self.toggleBtn.setText('关闭协同模式')

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)

        InfoBar.success('协同模式已开启',
                        '请确保戒指处于录音模式，长按录音后松开',
                        parent=self.window(), duration=3000,
                        position=InfoBarPosition.TOP_RIGHT)

        # Start waiting for audio
        self.__waitForAudio()

    def __stopCollabMode(self):
        self._active = False

        # Stop any AudioReceiveThread first (signal it to exit its loop)
        for t in self._workerThreads:
            if isinstance(t, AudioReceiveThread):
                t.stop()
        # Then quit all remaining workers
        for t in self._workerThreads:
            if t.isRunning():
                t.quit()
                t.wait(2000)
        self._workerThreads.clear()

        self._removeStatusBubble()
        if self._thinkingWidget:
            self._removeThinking()

        self.statusLabel.setText('协同模式未开启')
        self.statusLabel.setProperty('active', False)
        self.toggleBtn.setText('开启协同模式')

        self.statusLabel.style().unpolish(self.statusLabel)
        self.statusLabel.style().polish(self.statusLabel)
        signalBus.modeStopped.emit('collab')

    def __onOtherModeStarted(self, mode: str):
        if mode == 'collab' or not self._active:
            return
        self.toggleBtn.setChecked(False)
        InfoBar.info('协同模式已自动关闭', '已开启其他模式，协同模式自动退出',
                     parent=self.window(), duration=2000,
                     position=InfoBarPosition.TOP_RIGHT)

    # ── Audio → ASR → Chat pipeline ─────────────────────────────

    def __waitForAudio(self):
        """Start listening for audio from the ring."""
        if not self._active:
            return
        client = _get_shared_client()
        loop_thread = _get_loop_thread()
        if client is None or loop_thread is None:
            return

        self.statusLabel.setText('协同模式 - 请长按戒指录音...')
        self._addStatusBubble('等待录音中，长按戒指开始说话...')

        t = AudioReceiveThread(client, loop_thread, parent=self)
        t.audioReceived.connect(self.__onAudioReceived)
        t.error.connect(self.__onAudioError)
        t.finished.connect(lambda: self.__threadDone(t))
        t.start()
        self._workerThreads.append(t)

    def __onAudioReceived(self, file_index: int, raw_audio: bytes):
        """Audio received from ring - start ASR."""
        if not self._active:
            return

        self._removeStatusBubble()
        self.statusLabel.setText('协同模式 - 正在识别语音...')
        self._addThinking('正在语音识别...')

        api_key = cfg.get(cfg.stepFunApiKey)
        t = AsrWorkerThread(raw_audio, api_key, parent=self)
        t.transcriptionDone.connect(self.__onTranscription)
        t.error.connect(self.__onAsrError)
        t.finished.connect(lambda: self.__threadDone(t))
        t.start()
        self._workerThreads.append(t)

    def __onTranscription(self, text: str):
        """ASR completed - show text and start chat."""
        if not self._active:
            return

        self._removeThinking()

        # Add user bubble
        self._addChatBubble('user', text)
        self._chatHistory.append(('user', text))

        # Start chat streaming
        self.statusLabel.setText('协同模式 - AI 正在回答...')
        self._addThinking('正在思考...')

        api_key = cfg.get(cfg.stepFunApiKey)
        model = cfg.get(cfg.collabModel) or 'step-2-16k'
        system_prompt = cfg.get(cfg.collabSystemPrompt)

        t = ChatWorkerThread(text, api_key, model, system_prompt,
                             self._chatHistory, parent=self)
        t.textDelta.connect(self.__onTextDelta)
        t.finished.connect(self.__onChatFinished)
        t.error.connect(self.__onChatError)
        t.finished.connect(lambda: self.__threadDone(t))
        t.start()
        self._workerThreads.append(t)

    def __onTextDelta(self, delta: str):
        """Streaming text chunk from chat API."""
        if not self._active:
            return
        # Replace thinking with actual bubble on first delta
        if self._thinkingWidget:
            self._removeThinking()
            self._currentBubble = self._addChatBubble('assistant', '')
        if self._currentBubble:
            self._currentBubble.appendText(delta)
        self._scrollToBottom()

    def __onChatFinished(self):
        """Chat response completed."""
        if not self._active:
            return

        self._removeThinking()

        # Save assistant content to history
        if self._currentBubble:
            content = self._currentBubble.contentLabel.text()
            self._chatHistory.append(('assistant', content))

        self._currentBubble = None
        self.statusLabel.setText('协同模式 - 等待录音')
        self._scrollToBottom()

        # Wait for next audio
        QTimer.singleShot(500, self.__waitForAudio)

    def __onAudioError(self, error_msg: str):
        if not self._active:
            return
        self._removeStatusBubble()
        self._removeThinking()
        self.statusLabel.setText('协同模式 - 等待录音')
        # Retry after a delay
        QTimer.singleShot(1000, self.__waitForAudio)

    def __onAsrError(self, error_msg: str):
        if not self._active:
            return
        self._removeThinking()
        self._addChatBubble('assistant', f'[语音识别失败] {error_msg}')
        self.statusLabel.setText('协同模式 - 等待录音')
        QTimer.singleShot(500, self.__waitForAudio)

    def __onChatError(self, error_msg: str):
        if not self._active:
            return
        self._removeThinking()
        if self._currentBubble:
            self._currentBubble.appendText(f'\n\n[错误] {error_msg}')
        else:
            self._addChatBubble('assistant', f'[AI 回答失败] {error_msg}')
        self._currentBubble = None
        self.statusLabel.setText('协同模式 - 等待录音')
        QTimer.singleShot(500, self.__waitForAudio)

    # ── Chat UI helpers ──────────────────────────────────────────

    def _addChatBubble(self, role: str, text: str) -> ChatBubble:
        self._removeEmptyHint()
        bubble = ChatBubble(role, text, self.view)
        self.chatContainer.addWidget(bubble)
        self._scrollToBottom()
        return bubble

    def _addThinking(self, text: str):
        self._removeThinking()
        self._removeEmptyHint()
        self._thinkingWidget = ThinkingWidget(text, self.view)
        self.chatContainer.addWidget(self._thinkingWidget)
        self._scrollToBottom()

    def _removeThinking(self):
        if self._thinkingWidget:
            # Stop progress bar animation before removal
            if hasattr(self._thinkingWidget, 'progress'):
                self._thinkingWidget.progress.stop()
            self.chatContainer.removeWidget(self._thinkingWidget)
            self._thinkingWidget.deleteLater()
            self._thinkingWidget = None

    def _addStatusBubble(self, text: str):
        self._removeEmptyHint()
        w = QWidget(self.view)
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 0, 8, 0)
        label = CaptionLabel(text, w)
        label.setTextColor(QColor(150, 150, 150), QColor(120, 120, 120))
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        w.setProperty('isStatusBubble', True)
        self.chatContainer.addWidget(w)
        self._scrollToBottom()

    def _removeStatusBubble(self):
        for i in range(self.chatContainer.count()):
            item = self.chatContainer.itemAt(i)
            if item and item.widget() and item.widget().property('isStatusBubble'):
                w = item.widget()
                self.chatContainer.removeWidget(w)
                w.deleteLater()
                return

    def _removeEmptyHint(self):
        if self.emptyHint and self.emptyHint.parent() is not None:
            self.chatContainer.removeWidget(self.emptyHint)
            self.emptyHint.setVisible(False)

    def _scrollToBottom(self):
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()))

    # ── Connection callbacks ─────────────────────────────────────

    def __onDeviceConnected(self, name: str, address: str):
        self.connectionHint.setText('已连接设备，请确保戒指处于录音模式')
        self.toggleBtn.setEnabled(True)

    def __onDeviceDisconnected(self):
        if self._active:
            self.__stopCollabMode()
        self.connectionHint.setText('请先连接戒指设备')
        self.toggleBtn.setEnabled(False)
        self.toggleBtn.setChecked(False)

    # ── Helpers ──────────────────────────────────────────────────

    def __threadDone(self, thread):
        if thread in self._workerThreads:
            self._workerThreads.remove(thread)
