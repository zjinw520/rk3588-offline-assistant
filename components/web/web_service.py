#!/usr/bin/env python3
"""Offline LAN web UI for the RKLLama API running on the same board."""

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, request


RKLLAMA_URL = "http://127.0.0.1:8080"
DEFAULT_MODEL = "qwen2-0.5b"
WEB_PORT = 8081
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 600
VOICE_RUNNER = "/userdata/voice-assistant/run_voice_assistant.sh"
VOICE_PID_FILE = Path("/userdata/voice-assistant/voice-assistant.pid")
VOICE_LOG_FILE = Path("/userdata/voice-assistant/voice-assistant.log")
VOICE_STATE_FILE = Path("/userdata/voice-assistant/voice-assistant-state.json")
VOICE_PROCESS = None
BROWSER_PROFILE = b"/userdata/voice-assistant/chromium-profile"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024


PAGE = '<!doctype html>\n<html lang="zh-CN" data-build="web-v081">\n<head>\n  <meta charset="utf-8">\n  <meta name="viewport" content="width=device-width,initial-scale=1">\n  <meta name="color-scheme" content="light">\n  <title>小助手 · RK3588 离线 AI</title>\n  <style>\n    :root{--bg:#f3f6fb;--paper:#fff;--ink:#17283f;--muted:#627086;--line:#dce4ee;--accent:#2861dc;--soft:#e6eeff;--green:#24775b;--danger:#a62f45;--radius:20px;color-scheme:light}\n    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:15px;line-height:1.55}\n    button,textarea,select{font:inherit}button{cursor:pointer;-webkit-tap-highlight-color:transparent}button:disabled{cursor:default;opacity:.48}button:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #9bb9ff;outline-offset:3px}\n    button{border:1px solid var(--line);background:var(--paper);color:var(--ink);min-height:46px;border-radius:13px;padding:10px 16px}button:hover:not(:disabled){border-color:#a9bee5}.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}.primary:hover:not(:disabled){background:#1f53c6}.quiet{background:transparent;border-color:transparent;color:var(--muted)}.wide{width:100%}h1,h2,h3,p{margin:0}svg{width:22px;height:22px;flex-shrink:0;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}\n    .shell{width:min(1080px,100%);padding:30px;margin:auto}.header{display:flex;align-items:center;justify-content:space-between;gap:18px}.brand{display:flex;gap:13px;align-items:center}.brand-mark{width:48px;height:48px;border-radius:15px;background:var(--accent);color:#fff;display:grid;place-items:center}.brand-mark svg{width:27px;height:27px}.brand h1{font-size:27px;letter-spacing:1px;line-height:1.25}.brand p{font-size:12px;color:var(--muted);margin-top:5px}.header-actions{display:flex;gap:10px;align-items:center}.settings-button{display:flex;align-items:center;gap:8px}.ready{margin:19px 0;display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:12px}.ready-left{display:flex;align-items:center;gap:7px}.dot{width:7px;height:7px;background:#8c9aac;border-radius:50%;display:inline-block}.dot.ok{background:var(--green)}.dot.bad{background:var(--danger)}\n    .layout{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:22px;align-items:start}.chat-panel{min-width:0}.model-switch{width:100%;display:flex;justify-content:space-between;align-items:center;text-align:left;padding:14px 18px;min-height:76px;margin-bottom:20px;border-radius:17px}.eyebrow{font-size:11px;color:var(--muted);letter-spacing:.4px;display:block}.model-name{display:block;font-size:16px;font-weight:600;margin-top:4px;overflow-wrap:anywhere}.section-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:8px 0 15px}.section-head h2{font-size:14px;font-weight:600}.section-head span{font-size:12px;color:var(--muted)}\n    .chat{height:440px;min-height:300px;overflow:auto;padding:4px 5px 18px;scrollbar-width:thin;overscroll-behavior:contain}.welcome{height:100%;min-height:260px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:13px;text-align:center;color:var(--muted)}.welcome svg{color:var(--accent);width:32px;height:32px;margin-bottom:3px}.welcome h3{font-weight:500;color:var(--ink);font-size:22px}.welcome p{font-size:13px;line-height:1.8}.message{display:flex;flex-direction:column;gap:8px;align-items:flex-start;margin:0 0 23px}.message.user{align-items:flex-end}.author{color:var(--muted);font-size:12px}.bubble{padding:16px 19px;border:1px solid var(--line);border-radius:5px 18px 18px;background:var(--paper);font-size:16px;line-height:1.85;white-space:pre-wrap;overflow-wrap:anywhere;max-width:96%}.user .bubble{background:var(--soft);border:0;border-radius:18px 18px 5px;max-width:88%}.message.error .bubble{color:var(--danger)}.message.pending .bubble{color:var(--muted)}\n    .composer{border:1px solid var(--line);border-radius:18px;background:var(--paper);padding:12px;margin-top:8px}.composer textarea{display:block;width:100%;min-height:75px;max-height:220px;border:0;background:transparent;resize:vertical;color:var(--ink);font-size:16px;line-height:1.6;padding:6px}.composer textarea:focus-visible{outline-offset:0}.composer-actions{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-top:8px}.composer-hint{color:var(--muted);font-size:11px}.send{min-width:104px;display:flex;align-items:center;justify-content:center;gap:8px}.send svg{width:18px;height:18px}.feedback{font-size:12px;min-height:22px;color:var(--muted);padding:8px 4px 0;overflow-wrap:anywhere}.feedback.error{color:var(--danger)}\n    .voice-panel{background:var(--paper);border:1px solid var(--line);border-radius:20px;padding:23px;position:relative}.voice-kicker{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px}.voice-kicker svg{width:18px;height:18px}.voice-mark{width:64px;height:64px;background:var(--soft);color:var(--accent);border-radius:21px;display:grid;place-items:center;margin:25px 0 20px}.voice-mark svg{width:29px;height:29px}.voice-title{font-size:20px;font-weight:500}.voice-state{font-size:13px;color:var(--muted);line-height:1.8;margin:9px 0 18px;min-height:70px;overflow-wrap:anywhere}.voice-action{min-height:53px;font-size:16px}.voice-details{font-size:12px;color:var(--muted);margin-top:12px;text-align:center}.voice-caption{margin-top:20px;padding-top:18px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);line-height:1.8}.footer{display:flex;justify-content:space-between;gap:12px;margin-top:28px;color:var(--muted);font-size:11px;flex-wrap:wrap}.loading-copy{font-size:12px;color:var(--muted);margin-bottom:12px;min-height:19px}\n    dialog{color:var(--ink);background:var(--paper);border:1px solid var(--line);border-radius:24px;padding:0;width:min(500px,calc(100% - 28px));max-height:calc(100vh - 32px);max-height:calc(100dvh - 32px);box-shadow:0 20px 80px #17283f30}dialog::backdrop{background:#18283f66}.dialog-body{padding:25px;overflow:auto;max-height:calc(100vh - 116px);max-height:calc(100dvh - 116px)}.dialog-heading{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:24px}.dialog-heading h2{font-size:24px;font-weight:500;margin-top:4px}.close-button{width:44px;min-width:44px;height:44px;padding:0;display:grid;place-items:center}.field-title{color:var(--muted);font-size:13px;margin:18px 0 10px}.model-option{width:100%;display:flex;align-items:center;justify-content:space-between;text-align:left;padding:15px 16px;border-radius:15px;margin-bottom:11px;gap:12px}.model-option strong{font-size:15px;font-weight:600;display:block;overflow-wrap:anywhere}.model-option small{font-size:12px;color:var(--muted);display:block;margin-top:5px}.model-option[aria-pressed="true"]{border-color:var(--accent);background:var(--soft)}.check{width:18px;height:18px;visibility:hidden;color:var(--accent)}.model-option[aria-pressed="true"] .check{visibility:visible}.settings-hint{font-size:12px;color:var(--muted);line-height:1.7}.select-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;margin:20px 0}.select-grid label{display:flex;flex-direction:column;gap:9px;font-size:13px;color:var(--muted)}select{width:100%;min-height:48px;padding:9px 12px;border:1px solid var(--line);border-radius:12px;color:var(--ink);background:var(--bg);font-size:16px}.clear-button{width:100%;margin:0 0 13px}.diagnostics{border-top:1px solid var(--line);padding-top:12px;color:var(--muted);font-size:12px}.diagnostics summary{cursor:pointer;min-height:44px;padding:10px 0}.diagnostics pre{white-space:pre-wrap;overflow-wrap:anywhere;font-family:inherit;font-size:12px;line-height:1.7;background:var(--bg);padding:12px;border-radius:12px;max-height:220px;overflow:auto}.dialog-footer{background:var(--paper);padding:14px 25px 22px;border-top:1px solid var(--line)}\n    @media(min-width:900px){.chat{height:480px}}@media(max-width:760px){.shell{padding:22px 16px}.layout{grid-template-columns:minmax(0,1fr);gap:18px}.header-actions .settings-button span{display:none}.settings-button{padding:10px 12px}.brand h1{font-size:24px}.brand p{font-size:11px}.ready{font-size:11px}.chat{height:390px}.voice-panel{padding:18px;display:grid;grid-template-columns:46px minmax(0,1fr);gap:0 13px}.voice-kicker{grid-column:1/-1;margin-bottom:14px}.voice-mark{grid-column:1;grid-row:2/4;width:46px;height:46px;border-radius:14px;margin:0}.voice-mark svg{width:23px;height:23px}.voice-title{font-size:17px;align-self:center}.voice-state{grid-column:2;font-size:12px;margin:5px 0 14px;min-height:0}.voice-action,.voice-details,.voice-caption{grid-column:1/-1}.voice-caption{margin-top:15px;padding-top:13px}.footer{margin-top:22px}.composer-hint{max-width:62%;font-size:11px}.model-switch{margin-bottom:17px}.bubble{font-size:16px}.dialog-body{padding:22px}.dialog-footer{padding:13px 22px 20px}}\n    @media(max-width:360px){.shell{padding:18px 12px}.ready{gap:7px}.ready .offline-note{font-size:10px}.brand-mark{width:41px;height:41px}.brand{gap:10px}.brand h1{font-size:22px}.brand p{font-size:10px}.model-name{font-size:15px}.bubble{padding:13px 15px}.chat{height:370px}.welcome h3{font-size:20px}.select-grid{gap:10px}.dialog-body{padding:18px}.dialog-footer{padding:13px 18px 18px}}\n  </style>\n</head>\n<body>\n  <main class="shell" id="web-v081">\n    <header class="header">\n      <div class="brand"><div class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 9v6M8 6v12M12 3v18M16 7v10M20 5v14"/></svg></div><div><h1>小助手</h1><p>RK3588 · 离线 AI 助手</p></div></div>\n      <div class="header-actions"><button type="button" id="openSettings" class="settings-button" aria-haspopup="dialog" aria-label="设置"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18M8 3v6M16 9v6M6 15v6"/></svg><span>设置</span></button></div>\n    </header>\n    <div class="ready"><div class="ready-left"><span id="serviceDot" class="dot"></span><span id="serviceStatus">正在连接本地服务</span></div><span class="offline-note">局域网访问 · 开发板本地推理</span></div>\n    <div class="layout">\n      <section class="chat-panel" aria-label="文字对话">\n        <button type="button" class="model-switch" id="openModels" aria-haspopup="dialog"><span><span class="eyebrow">本页选择的模型</span><span class="model-name" id="currentModel">读取模型中…</span></span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>\n        <div class="section-head"><h2>本次文字对话</h2><span>仅保留在当前页面</span></div>\n        <div class="chat" id="chat" role="log" aria-label="文字问答记录" aria-live="polite">\n          <div class="welcome" id="welcome"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3Z"/></svg><h3>你好，有什么想聊的？</h3><p>在下方输入问题，开始文字聊天。<br>语音按钮控制开发板的麦克风与扬声器。</p></div>\n        </div>\n        <div class="composer">\n          <textarea id="input" rows="2" aria-label="输入问题" placeholder="输入你想问的问题…" maxlength="4000"></textarea>\n          <div class="composer-actions"><span class="composer-hint">Enter 发送 · Shift+Enter 换行</span><button id="send" class="primary send" type="button" disabled><span>发送</span><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 7-7 7 7M12 5v15"/></svg></button></div>\n        </div>\n        <p class="feedback" id="feedback" aria-live="polite"></p>\n      </section>\n      <aside class="voice-panel" aria-label="开发板语音控制">\n        <div class="voice-kicker"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>开发板语音</div>\n        <div class="voice-mark" aria-hidden="true"><svg viewBox="0 0 24 24"><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M6 10v2a6 6 0 0 0 12 0v-2M12 18v4M9 22h6"/></svg></div>\n        <h2 id="voiceTitle" class="voice-title">正在读取状态</h2>\n        <p class="voice-state" id="voiceState" aria-live="polite">请稍候，正在连接本机语音控制服务。</p>\n        <button type="button" id="voiceToggle" class="primary wide voice-action" disabled>开始语音对话</button>\n        <div class="voice-details" id="voiceSummary">启动设置：音色 2 · 语速 1.25×</div>\n        <p class="voice-caption">使用开发板上的麦克风和扬声器。启动后，请对开发板说“你好小助手”，听到提示音后提问。手机本页可直接输入文字。</p>\n      </aside>\n    </div>\n    <footer class="footer"><span>文字问答与语音推理均在开发板完成</span><span>Web v0.8.1</span></footer>\n  </main>\n  <dialog id="settings" aria-labelledby="settingsTitle">\n    <div class="dialog-body">\n      <div class="dialog-heading"><div><span class="eyebrow">按你的习惯</span><h2 id="settingsTitle">对话设置</h2></div><button type="button" class="close-button" id="closeSettings" aria-label="关闭设置"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></div>\n      <div class="field-title">选择模型</div>\n      <button type="button" class="model-option" data-model="qwen2-0.5b" aria-pressed="true"><span><strong>Qwen2 · 0.5B</strong><small>日常问答 · 响应更轻快</small></span><svg class="check" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg></button>\n      <button type="button" class="model-option" data-model="deepseek-r1-1.5b" aria-pressed="false"><span><strong>DeepSeek-R1 · 1.5B</strong><small>尝试推理 · 等待时间较长</small></span><svg class="check" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg></button>\n      <p class="settings-hint" id="settingsHint">切换模型会清空当前页面的文字上下文。请先停止语音，再修改设置。</p>\n      <div class="select-grid"><label>音色<select id="speaker"><option value="0">音色 0</option><option value="1">音色 1</option><option value="2" selected>音色 2</option><option value="3">音色 3</option><option value="4">音色 4</option></select></label><label>语速<select id="voiceSpeed"><option value="1">1.0×</option><option value="1.15">1.15×</option><option value="1.25" selected>1.25×</option><option value="1.35">1.35×</option></select></label></div>\n      <button type="button" id="clear" class="clear-button">清空本页文字对话</button>\n      <details class="diagnostics"><summary>开发板语音日志</summary><pre id="voiceLog">尚未读取到日志。</pre></details>\n    </div>\n    <div class="dialog-footer"><button type="button" id="doneSettings" class="primary wide">完成</button></div>\n  </dialog>\n  <script>\n  (() => {\n    \'use strict\';\n    const el = id => document.getElementById(id);\n    const state = {online:false, models:[], model:\'qwen2-0.5b\', messages:[], textBusy:false, voiceBusy:false, voiceRunning:false, voiceKnown:false, voicePid:null, statusPending:false, voicePending:false, revision:0, voiceError:\'\', settingsTrigger:null};\n    const labels = {\'qwen2-0.5b\':\'Qwen2 · 0.5B\',\'deepseek-r1-1.5b\':\'DeepSeek-R1 · 1.5B\'};\n    const modelButtons = Array.from(document.querySelectorAll(\'[data-model]\'));\n    const dialog = el(\'settings\');\n\n    function feedback(text, error=false) { el(\'feedback\').textContent=text; el(\'feedback\').classList.toggle(\'error\',error); }\n    function render() {\n      const locked = state.textBusy || state.voiceBusy || state.voiceRunning;\n      el(\'currentModel\').textContent = labels[state.model] || state.model || \'暂未找到模型\';\n      el(\'serviceDot\').className=\'dot \'+(state.online?\'ok\':\'bad\');\n      el(\'serviceStatus\').textContent=state.online?\'本地服务已就绪\':\'模型服务暂不可用\';\n      el(\'send\').disabled=!state.online || state.textBusy || state.voiceBusy || state.voiceRunning || !state.model;\n      el(\'send\').querySelector(\'span\').textContent=state.textBusy?\'等待回答…\':\'发送\';\n      el(\'input\').disabled=state.textBusy || state.voiceBusy || state.voiceRunning;\n      el(\'input\').placeholder=state.voiceRunning?\'请先停止板载语音，再进行文字问答\':\'输入你想问的问题…\';\n      el(\'voiceToggle\').disabled=state.textBusy || state.voiceBusy || (!state.voiceKnown && !state.voiceRunning) || (!state.online && !state.voiceRunning);\n      el(\'voiceToggle\').textContent=state.voiceBusy?\'处理中…\':(state.voiceRunning?\'停止语音对话\':\'开始语音对话\');\n      el(\'voiceToggle\').classList.toggle(\'primary\',!state.voiceRunning);\n      if(!state.voiceBusy){\n        el(\'voiceTitle\').textContent=state.voiceKnown?(state.voiceRunning?\'语音运行中\':\'语音已停止\'):\'语音状态暂不可用\';\n        el(\'voiceState\').textContent=state.voiceError || (state.voiceRunning?\'说“你好小助手”，听到提示音后提问。运行中可能正在等待、生成或播放。\':\'开始后，对开发板说“你好小助手”唤醒。\');\n      }\n      el(\'voiceSummary\').textContent=\'启动设置：音色 \'+el(\'speaker\').value+\' · 语速 \'+el(\'voiceSpeed\').value+\'×\';\n      modelButtons.forEach(button=>{button.disabled=locked || !state.models.includes(button.dataset.model); button.setAttribute(\'aria-pressed\',String(button.dataset.model===state.model));});\n      el(\'speaker\').disabled=locked; el(\'voiceSpeed\').disabled=locked;\n      el(\'clear\').disabled=state.textBusy || state.voiceBusy;\n      el(\'settingsHint\').textContent=locked?\'请先停止语音，并等待文字回答完成，再修改设置。\':\'切换模型会清空本页文字上下文。选择完成后关闭设置即可。\';\n    }\n    async function api(path, options={}, timeout=10000) {\n      const controller=new AbortController();\n      const timer=setTimeout(()=>controller.abort(),timeout);\n      try {\n        const response=await fetch(path,{cache:\'no-store\',...options,signal:controller.signal});\n        let data; try {data=await response.json();} catch {throw new Error(\'服务未返回有效数据，请刷新后重试\');}\n        if(!response.ok) throw new Error(data.error || \'HTTP \'+response.status);\n        if(!data || typeof data!==\'object\') throw new Error(\'服务状态格式异常\');\n        return data;\n      } catch(error){ if(error.name===\'AbortError\')throw new Error(\'请求等待超时，请稍后重试\'); throw error; }\n      finally{clearTimeout(timer);}\n    }\n    function post(path,body,timeout){return api(path,{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(body)},timeout);}\n    function hideReasoning(text, model) {\n      let value=String(text).replace(/&lt;\\s*(\\/?)think\\s*&gt;/gi,\'<$1think>\');\n      let depth=0, output=\'\';\n      for(const token of value.split(/(<\\s*\\/?\\s*think\\s*>)/gi)){\n        if(/^<\\s*think\\s*>$/i.test(token)){depth++;}\n        else if(/^<\\s*\\/\\s*think\\s*>$/i.test(token)){if(depth)depth--;else if(model.startsWith(\'deepseek-r1\'))output=\'\';}\n        else if(!depth){output+=token;}\n      }\n      return output.replace(/<\\s*\\/?\\s*(?:t(?:h(?:i(?:n(?:k)?)?)?)?)?\\s*$/i,\'\').trim();\n    }\n    function message(role,text,kind=\'\'){\n      el(\'welcome\')?.remove();\n      const row=document.createElement(\'div\');row.className=\'message \'+role+(kind?\' \'+kind:\'\');\n      const author=document.createElement(\'span\');author.className=\'author\';author.textContent=role===\'user\'?\'你\':\'小助手\';\n      const bubble=document.createElement(\'div\');bubble.className=\'bubble\';bubble.textContent=text;\n      row.append(author,bubble);el(\'chat\').appendChild(row);el(\'chat\').scrollTop=el(\'chat\').scrollHeight;\n      return {row,bubble};\n    }\n    function resetChat(){state.messages=[];el(\'chat\').replaceChildren();const welcome=document.createElement(\'div\');welcome.id=\'welcome\';welcome.className=\'welcome\';const title=document.createElement(\'h3\');title.textContent=\'开始一个新话题\';const hint=document.createElement(\'p\');hint.textContent=\'在下方输入问题，我会在开发板上回答。\';welcome.append(title,hint);el(\'chat\').appendChild(welcome);}\n    async function refreshService(){\n      if(state.statusPending)return;state.statusPending=true;\n      try{const data=await api(\'/api/status\',{},7000);state.models=Array.isArray(data.models)?data.models.filter(x=>typeof x===\'string\'&&labels[x]):[];state.online=Boolean(data.online&&state.models.length);if(!state.models.includes(state.model)&&!state.textBusy&&!state.voiceRunning&&!state.voiceBusy){const previous=state.model;state.model=state.models.includes(data.model)?data.model:(state.models[0]||\'\');if(previous!==state.model&&state.messages.length){resetChat();feedback(\'可用模型已变化，本页文字上下文已清空。\');}}}\n      catch{state.online=false;}finally{state.statusPending=false;render();}\n    }\n    async function refreshVoice(){\n      if(state.voicePending||state.voiceBusy)return;state.voicePending=true;const revision=state.revision;\n      try{const data=await api(\'/api/voice/status\',{},7000);if(revision!==state.revision||state.voiceBusy)return;if(typeof data.running!==\'boolean\')throw new Error(\'语音状态格式异常\');state.voiceKnown=true;state.voiceRunning=data.running;state.voicePid=data.pid||null;state.voiceError=\'\';if(typeof data.last_log===\'string\')el(\'voiceLog\').textContent=data.last_log||\'暂无语音日志。\';}\n      catch(error){if(revision===state.revision&&!state.voiceBusy){state.voiceKnown=false;state.voiceError=\'无法确认语音状态：\'+error.message;}}\n      finally{state.voicePending=false;render();}\n    }\n    async function submit(){\n      const content=el(\'input\').value.trim();if(!content||el(\'send\').disabled)return;\n      state.textBusy=true;const chosen=state.model;const history=[...state.messages,{role:\'user\',content}];message(\'user\',content);el(\'input\').value=\'\';const pending=message(\'assistant\',\'正在本地生成回答，请稍候…\',\'pending\');feedback(\'首次加载模型或使用 DeepSeek 时，可能需要等待较久。\');render();\n      try{const data=await post(\'/api/chat\',{model:chosen,messages:history},630000);const raw=data.message&&typeof data.message.content===\'string\'?data.message.content:\'\';const answer=hideReasoning(raw,chosen);if(!answer)throw new Error(\'模型没有返回可显示的正式回答\');pending.row.classList.remove(\'pending\');pending.bubble.textContent=answer;state.messages=[...history,{role:\'assistant\',content:answer}].slice(-12);feedback(\'回答已完成。\');}\n      catch(error){pending.row.classList.remove(\'pending\');pending.row.classList.add(\'error\');pending.bubble.textContent=\'请求失败：\'+error.message;feedback(\'本次失败内容不会加入后续对话上下文。\',true);}\n      finally{state.textBusy=false;render();el(\'chat\').scrollTop=el(\'chat\').scrollHeight;if(!state.voiceRunning)el(\'input\').focus({preventScroll:true});}\n    }\n    async function toggleVoice(){\n      if(el(\'voiceToggle\').disabled)return;const stop=state.voiceRunning;state.voiceBusy=true;state.revision++;state.voiceError=\'\';render();el(\'voiceTitle\').textContent=stop?\'正在停止语音\':\'正在启动语音\';el(\'voiceState\').textContent=stop?\'正在停止开发板上的语音进程…\':\'正在准备本机语音资源，请稍候…\';\n      try{const data=stop?await post(\'/api/voice/stop\',{},12000):await post(\'/api/voice/start\',{model:state.model,speaker:Number(el(\'speaker\').value),speed:Number(el(\'voiceSpeed\').value),max_speech_chars:1000},20000);state.voiceKnown=true;state.voiceRunning=!stop;state.voicePid=stop?null:(data.pid||null);feedback(stop?\'板载语音已停止。\':\'板载语音已启动，请等待就绪后说“你好小助手”。\');}\n      catch(error){state.voiceError=(stop?\'停止失败：\':\'启动失败：\')+error.message;feedback(state.voiceError,true);}\n      finally{state.voiceBusy=false;render();await refreshVoice();}\n    }\n    function openSettings(trigger){state.settingsTrigger=trigger;render();if(!dialog.open)dialog.showModal();el(\'closeSettings\').focus();}\n    function closeSettings(){dialog.close();state.settingsTrigger?.focus();}\n    el(\'openSettings\').addEventListener(\'click\',()=>openSettings(el(\'openSettings\')));el(\'openModels\').addEventListener(\'click\',()=>openSettings(el(\'openModels\')));\n    el(\'closeSettings\').addEventListener(\'click\',closeSettings);el(\'doneSettings\').addEventListener(\'click\',closeSettings);\n    dialog.addEventListener(\'cancel\',event=>{event.preventDefault();closeSettings();});\n    dialog.addEventListener(\'click\',event=>{if(event.target===dialog){const bounds=dialog.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)closeSettings();}});\n    dialog.addEventListener(\'keydown\',event=>{if(event.key!==\'Tab\')return;const items=Array.from(dialog.querySelectorAll(\'button:not(:disabled),select:not(:disabled),summary\')).filter(x=>x.getClientRects().length);const current=items.indexOf(document.activeElement);if(event.shiftKey&&current<=0){event.preventDefault();items[items.length-1].focus();}else if(!event.shiftKey&&current===items.length-1){event.preventDefault();items[0].focus();}});\n    modelButtons.forEach(button=>button.addEventListener(\'click\',()=>{if(button.disabled||button.dataset.model===state.model)return;state.model=button.dataset.model;resetChat();feedback(\'已切换到 \'+labels[state.model]+\'，文字上下文已清空。\');render();}));\n    el(\'speaker\').addEventListener(\'change\',render);el(\'voiceSpeed\').addEventListener(\'change\',render);\n    el(\'clear\').addEventListener(\'click\',()=>{if(el(\'clear\').disabled)return;resetChat();feedback(\'本页文字对话已清空。\');});\n    el(\'send\').addEventListener(\'click\',submit);el(\'voiceToggle\').addEventListener(\'click\',toggleVoice);\n    let composing=false;el(\'input\').addEventListener(\'compositionstart\',()=>composing=true);el(\'input\').addEventListener(\'compositionend\',()=>composing=false);\n    el(\'input\').addEventListener(\'keydown\',event=>{if(event.key===\'Enter\'&&!event.shiftKey&&!event.isComposing&&!composing&&event.keyCode!==229){event.preventDefault();submit();}});\n    render();refreshService();refreshVoice();setInterval(refreshService,15000);setInterval(refreshVoice,2500);\n  })();\n  </script>\n</body>\n</html>\n'


def clean_messages(value):
    if not isinstance(value, list):
        raise ValueError("messages 必须是数组")
    normalized = []
    for item in value[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        content = content.strip()
        if content:
            normalized.append({"role": role, "content": content})
    if not normalized or normalized[-1]["role"] != "user":
        raise ValueError("最后一条消息必须来自用户")

    # The current Modelfile uses NUM_CTX=1024 and reserves output tokens. Keep the
    # most recent conversation while applying a conservative character budget.
    remaining = MAX_HISTORY_CHARS
    selected = []
    for item in reversed(normalized):
        if remaining <= 0:
            break
        content = item["content"][:remaining]
        if content:
            selected.append({"role": item["role"], "content": content})
            remaining -= len(content)
    return list(reversed(selected))


def upstream_json(path, payload=None, timeout=600):
    body = None
    headers = {}
    method = "GET"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    upstream = Request(
        RKLLAMA_URL + path,
        data=body,
        headers=headers,
        method=method,
    )
    with urlopen(upstream, timeout=timeout) as response:
        return response.status, response.read()


def available_models():
    _, raw = upstream_json("/api/tags", timeout=5)
    data = json.loads(raw.decode("utf-8"))
    return [item.get("name") for item in data.get("models", [])
            if isinstance(item.get("name"), str)]


def voice_pid():
    global VOICE_PROCESS
    if VOICE_PROCESS is not None:
        if VOICE_PROCESS.poll() is None:
            return VOICE_PROCESS.pid
        VOICE_PROCESS = None
        VOICE_PID_FILE.unlink(missing_ok=True)
    try:
        pid = int(VOICE_PID_FILE.read_text(encoding="ascii").strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        VOICE_PID_FILE.unlink(missing_ok=True)
        return None


def voice_state():
    try:
        data = json.loads(VOICE_STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def voice_log_tail(limit=1600):
    try:
        with VOICE_LOG_FILE.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit), os.SEEK_SET)
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def terminate_app_browser():
    for process_dir in Path("/proc").iterdir():
        if not process_dir.name.isdigit():
            continue
        try:
            command = (process_dir / "cmdline").read_bytes()
            if b"chromium" in command and BROWSER_PROFILE in command:
                os.kill(int(process_dir.name), signal.SIGTERM)
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue


@app.get("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.get("/api/status")
def status():
    try:
        names = available_models()
        selected = DEFAULT_MODEL if DEFAULT_MODEL in names else (names[0] if names else "")
        return jsonify(online=bool(names), model=selected, models=names)
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        return jsonify(online=False, error=str(error)), 503


@app.post("/api/chat")
def chat_proxy():
    try:
        incoming = request.get_json(silent=False)
        messages = clean_messages(incoming.get("messages") if isinstance(incoming, dict) else None)
        requested_model = incoming.get("model", DEFAULT_MODEL)
        names = available_models()
        if not isinstance(requested_model, str) or requested_model not in names:
            raise ValueError("模型不存在或尚未被RKLLama识别")
        payload = {"model": requested_model, "messages": messages, "stream": False}
        status_code, raw = upstream_json("/api/chat", payload=payload)
        return Response(raw, status=status_code, mimetype="application/json")
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except HTTPError as error:
        raw = error.read()
        return Response(raw or json.dumps({"error": str(error)}), status=error.code,
                        mimetype="application/json")
    except (URLError, TimeoutError) as error:
        return jsonify(error="无法连接RKLLama：{}".format(error)), 502
    except Exception as error:  # Keep the browser response useful on minimal Buildroot systems.
        app.logger.exception("Unexpected proxy failure")
        return jsonify(error="网页服务内部错误：{}".format(error)), 500


@app.post("/api/app/exit")
def app_exit():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="退出按钮只能在开发板本机使用"), 403
    timer = threading.Timer(0.4, terminate_app_browser)
    timer.daemon = True
    timer.start()
    return jsonify(ok=True)


@app.get("/api/voice/status")
def voice_status():
    pid = voice_pid()
    state = voice_state()
    return jsonify(
        running=pid is not None,
        pid=pid,
        model=state.get("model", ""),
        last_log=voice_log_tail(),
    )


@app.post("/api/voice/start")
def voice_start():
    global VOICE_PROCESS
    if voice_pid() is not None:
        return jsonify(error="语音助手已经在运行"), 409
    if not Path(VOICE_RUNNER).is_file():
        return jsonify(error="没有找到语音助手启动脚本"), 500

    incoming = request.get_json(silent=True) or {}
    model = incoming.get("model", DEFAULT_MODEL)
    try:
        speaker = int(incoming.get("speaker", 2))
        speed = float(incoming.get("speed", 1.25))
        max_chars = int(incoming.get("max_speech_chars", 1000))
        if model not in available_models():
            raise ValueError("所选模型不存在")
        if not 0 <= speaker <= 4:
            raise ValueError("音色编号必须在0到4之间")
        if not 0.5 <= speed <= 2.0:
            raise ValueError("语速必须在0.5到2.0之间")
        if not 20 <= max_chars <= 1000:
            raise ValueError("播报字数必须在20到1000之间")
    except (TypeError, ValueError) as error:
        return jsonify(error=str(error)), 400

    VOICE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "/bin/sh", VOICE_RUNNER,
        "--auto",
        "--model", model,
        "--speaker", str(speaker),
        "--speed", str(speed),
        "--max-speech-chars", str(max_chars),
    ]
    try:
        with VOICE_LOG_FILE.open("wb") as log_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(VOICE_LOG_FILE.parent),
                start_new_session=True,
            )
        VOICE_PROCESS = process
        VOICE_PID_FILE.write_text(str(process.pid), encoding="ascii")
        VOICE_STATE_FILE.write_text(
            json.dumps({"model": model, "speaker": speaker, "speed": speed},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        time.sleep(0.4)
        if process.poll() is not None:
            VOICE_PID_FILE.unlink(missing_ok=True)
            return jsonify(error="语音助手启动后立即退出：" + voice_log_tail()), 500
        return jsonify(ok=True, pid=process.pid, model=model)
    except OSError as error:
        VOICE_PID_FILE.unlink(missing_ok=True)
        return jsonify(error="无法启动语音助手：{}".format(error)), 500


@app.post("/api/voice/stop")
def voice_stop():
    global VOICE_PROCESS
    pid = voice_pid()
    if pid is None:
        return jsonify(ok=True, running=False)
    try:
        os.killpg(pid, signal.SIGTERM)
        for _ in range(20):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            os.killpg(pid, signal.SIGKILL)
        if VOICE_PROCESS is not None and VOICE_PROCESS.pid == pid:
            try:
                VOICE_PROCESS.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            VOICE_PROCESS = None
    except ProcessLookupError:
        pass
    except OSError as error:
        return jsonify(error="无法停止语音助手：{}".format(error)), 500
    finally:
        VOICE_PID_FILE.unlink(missing_ok=True)
    return jsonify(ok=True, running=False)


if __name__ == "__main__":
    print("RKLLama Web: http://0.0.0.0:{}".format(WEB_PORT), flush=True)
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False, threaded=True)
