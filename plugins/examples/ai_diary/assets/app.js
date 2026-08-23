/* AI 写日记页面逻辑（48a 桥 SDK 用法示例） */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var statusEl = $('status');
  var resultEl = $('result');

  function setStatus(msg) {
    statusEl.textContent = msg || '';
  }

  async function loadAiList() {
    try {
      var list = await Ambrace.getAiList();
      var items = (list && list.items) || [];
      var sel = $('aiList');
      sel.innerHTML = '';
      if (!items.length) {
        var opt = document.createElement('option');
        opt.value = '';
        opt.textContent = '（暂无角色，使用自定义提示词）';
        sel.appendChild(opt);
        return;
      }
      items.forEach(function (c) {
        var o = document.createElement('option');
        o.value = c.id;
        o.textContent = c.name;
        sel.appendChild(o);
      });
    } catch (e) {
      setStatus('角色列表加载失败：' + e.message);
    }
  }

  function toggleMode() {
    var mode = $('mode').value;
    $('aiRow').style.display = mode === 'ai' ? '' : 'none';
  }

  async function writeDiary() {
    var input = $('input').value.trim();
    if (!input) {
      setStatus('请先写下今天的事～');
      return;
    }
    var btn = $('writeBtn');
    btn.disabled = true;
    setStatus('AI 正在写日记…');
    resultEl.style.display = 'none';
    try {
      var mode = $('mode').value;
      var text;
      if (mode === 'ai') {
        var aiId = $('aiList').value;
        if (!aiId) {
          setStatus('请先在「AI 伙伴」中选择一个角色（或切换为自定义提示词）');
          btn.disabled = false;
          return;
        }
        text = await Ambrace.ai({ aiId: Number(aiId), input: input, maxTokens: 800 });
      } else {
        text = await Ambrace.ai({ prompt: input, maxTokens: 800 });
      }
      resultEl.textContent = text;
      resultEl.style.display = 'block';
      setStatus('写完啦，可以保存到本插件存储');
    } catch (e) {
      setStatus('写日记失败：' + e.message);
    } finally {
      btn.disabled = false;
    }
  }

  async function saveDiary() {
    var text = resultEl.textContent || '';
    if (!text || resultEl.style.display === 'none') {
      setStatus('还没有可保存的日记');
      return;
    }
    try {
      await Ambrace.store.set('diary', {
        text: text,
        input: $('input').value.trim(),
        savedAt: new Date().toISOString()
      });
      setStatus('已保存到本插件存储 ✅');
    } catch (e) {
      setStatus('保存失败：' + e.message);
    }
  }

  async function loadDiary() {
    try {
      var v = await Ambrace.store.get('diary');
      if (v && v.text) {
        resultEl.textContent = v.text;
        resultEl.style.display = 'block';
        if (v.input) $('input').value = v.input;
        setStatus('已读取上次保存的日记 📂');
      } else {
        setStatus('还没有保存过的日记');
      }
    } catch (e) {
      setStatus('读取失败：' + e.message);
    }
  }

  $('mode').addEventListener('change', toggleMode);
  $('writeBtn').addEventListener('click', writeDiary);
  $('saveBtn').addEventListener('click', saveDiary);
  $('loadBtn').addEventListener('click', loadDiary);

  toggleMode();
  loadAiList();
})();
