<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
  maxLength: { type: Number, default: 1024 },
});

const emit = defineEmits(["update:modelValue"]);
const editor = ref(null);

const allowedTags = new Set([
  "B", "STRONG", "I", "EM", "U", "S", "STRIKE", "DEL",
  "A", "CODE", "PRE", "BLOCKQUOTE", "TG-SPOILER", "P", "BR", "DIV",
]);

function safeHref(value) {
  if (!value?.trim()) return "";
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function sanitizeHtml(value) {
  const documentFragment = new DOMParser().parseFromString(value || "", "text/html");
  function clean(node) {
    [...node.children].forEach((child) => {
      if (!allowedTags.has(child.tagName)) {
        clean(child);
        child.replaceWith(...child.childNodes);
        return;
      }
      const originalHref = child.tagName === "A"
        ? child.getAttribute("href") || ""
        : "";
      [...child.attributes].forEach((attribute) => child.removeAttribute(attribute.name));
      if (child.tagName === "A") {
        const href = safeHref(originalHref);
        if (href) {
          child.setAttribute("href", href);
          child.setAttribute("target", "_blank");
          child.setAttribute("rel", "noreferrer");
        }
      }
      clean(child);
    });
  }
  clean(documentFragment.body);
  return documentFragment.body.innerHTML;
}

const preview = computed(() => sanitizeHtml(props.modelValue));
const characterCount = computed(() => {
  const parsed = new DOMParser().parseFromString(props.modelValue || "", "text/html");
  return parsed.body.textContent?.length || 0;
});
const warningThreshold = computed(() => Math.floor(props.maxLength * 0.9));
const isNearLimit = computed(
  () => characterCount.value > warningThreshold.value
    && characterCount.value <= props.maxLength,
);
const isOverLimit = computed(() => characterCount.value > props.maxLength);

function sync() {
  const html = sanitizeHtml(editor.value?.innerHTML || "");
  if (editor.value && editor.value.innerHTML !== html) editor.value.innerHTML = html;
  emit("update:modelValue", html);
}

function focusEditor() {
  editor.value?.focus({ preventScroll: true });
}

function command(name, value = null) {
  focusEditor();
  document.execCommand(name, false, value);
  sync();
}

function activeFormatElements(tagName, range) {
  const selector = tagName.toLowerCase();
  const matchingElements = [...editor.value.querySelectorAll(selector)];
  const closestFormat = (node) => {
    const element = node.nodeType === Node.ELEMENT_NODE
      ? node
      : node.parentElement;
    const match = element?.closest(selector);
    return match && editor.value?.contains(match) ? match : null;
  };
  const start = closestFormat(range.startContainer);
  const end = closestFormat(range.endContainer);
  if (start && start === end) return [start];
  if (range.collapsed && !start && !end) return [];
  return matchingElements.filter(
    (element) => range.intersectsNode(element),
  );
}

function placeSelectionMarkers(range) {
  const start = document.createComment("selection-start");
  if (range.collapsed) {
    range.insertNode(start);
    return { start, end: null };
  }
  const end = document.createComment("selection-end");
  const endRange = range.cloneRange();
  endRange.collapse(false);
  endRange.insertNode(end);
  const startRange = range.cloneRange();
  startRange.collapse(true);
  startRange.insertNode(start);
  return { start, end };
}

function restoreSelection(markers, selection) {
  const range = document.createRange();
  if (markers.end) {
    range.setStartAfter(markers.start);
    range.setEndBefore(markers.end);
  } else {
    range.setStartAfter(markers.start);
    range.collapse(true);
  }
  selection.removeAllRanges();
  selection.addRange(range);
  markers.start.remove();
  markers.end?.remove();
}

function unwrapFormats(elements, selection, range) {
  const markers = placeSelectionMarkers(range);
  elements.forEach((element) => element.replaceWith(...element.childNodes));
  restoreSelection(markers, selection);
}

function toggleSelectionFormat(tagName, applyFormat, removeFormat = unwrapFormats) {
  focusEditor();
  const selection = window.getSelection();
  if (!selection?.rangeCount) return;
  const range = selection.getRangeAt(0);
  if (!editor.value?.contains(range.commonAncestorContainer)) return;
  const activeElements = activeFormatElements(tagName, range);
  if (activeElements.length) {
    removeFormat(activeElements, selection, range);
    sync();
    return;
  }
  applyFormat(range, selection);
  sync();
}

function toggleBlock(tagName) {
  toggleSelectionFormat(
    tagName,
    () => document.execCommand("formatBlock", false, tagName),
    () => document.execCommand("formatBlock", false, "div"),
  );
}

function toggleInline(tagName) {
  toggleSelectionFormat(tagName, (range, selection) => {
    const element = document.createElement(tagName);
    if (range.collapsed) {
      element.textContent = tagName === "tg-spoiler" ? "Скрытый текст" : "Текст";
      range.insertNode(element);
      range.selectNodeContents(element);
    } else {
      element.appendChild(range.extractContents());
      range.insertNode(element);
      range.selectNodeContents(element);
    }
    selection.removeAllRanges();
    selection.addRange(range);
  });
}

function createLink() {
  const value = window.prompt("Введите ссылку");
  if (!value) return;
  const href = safeHref(value);
  if (!href) {
    window.alert("Используйте ссылку, начинающуюся с http:// или https://");
    return;
  }
  command("createLink", href);
}

function onPaste(event) {
  const text = event.clipboardData?.getData("text/plain");
  if (text === undefined) return;
  event.preventDefault();
  document.execCommand("insertText", false, text);
}

onMounted(() => {
  if (editor.value) editor.value.innerHTML = sanitizeHtml(props.modelValue);
});

watch(
  () => props.modelValue,
  async (value) => {
    await nextTick();
    if (
      editor.value
      && document.activeElement !== editor.value
      && editor.value.innerHTML !== value
    ) {
      editor.value.innerHTML = sanitizeHtml(value);
    }
  },
);
</script>

<template>
  <section class="telegram-composer">
    <div class="composer-heading">
      <div>
        <h2>Текст публикации</h2>
        <p>Форматирование поддерживается Telegram.</p>
      </div>
      <span
        class="caption-length"
        :class="{
          'caption-length-warning': isNearLimit,
          'caption-length-error': isOverLimit,
        }"
        :aria-label="`Длина текста: ${characterCount} из ${props.maxLength}`"
      >
        {{ characterCount }} / {{ props.maxLength }}
      </span>
    </div>

    <div
      class="format-toolbar"
      role="toolbar"
      aria-label="Форматирование текста"
      @mousedown.prevent
    >
      <button type="button" title="Жирный" aria-label="Жирный" @click="command('bold')"><strong>B</strong></button>
      <button type="button" title="Курсив" aria-label="Курсив" @click="command('italic')"><em>I</em></button>
      <button type="button" title="Подчёркнутый" aria-label="Подчёркнутый" @click="command('underline')"><u>U</u></button>
      <button type="button" title="Зачёркнутый" aria-label="Зачёркнутый" @click="command('strikeThrough')"><s>S</s></button>
      <span class="toolbar-divider" aria-hidden="true"></span>
      <button type="button" title="Ссылка" @click="createLink">Ссылка</button>
      <button type="button" title="Цитата" @click="toggleBlock('blockquote')">Цитата</button>
      <button type="button" title="Код" @click="toggleInline('code')">Код</button>
      <button type="button" title="Блок кода" @click="toggleBlock('pre')">Блок</button>
      <button type="button" title="Спойлер" @click="toggleInline('tg-spoiler')">Спойлер</button>
      <span class="toolbar-divider" aria-hidden="true"></span>
      <button type="button" title="Очистить форматирование" @click="command('removeFormat')">Очистить</button>
    </div>

    <div class="composer-workspace">
      <div class="composer-pane">
        <label for="telegram-caption-editor">Редактор</label>
        <div
          id="telegram-caption-editor"
          ref="editor"
          class="telegram-editor"
          contenteditable="true"
          spellcheck="true"
          @input="sync"
          @blur="sync"
          @paste="onPaste"
        ></div>
      </div>
      <div class="composer-pane">
        <span class="pane-label">Предпросмотр</span>
        <div class="telegram-preview">
          <div v-if="preview" v-html="preview"></div>
          <span v-else class="empty-preview">Подпись пустая</span>
        </div>
      </div>
    </div>
  </section>
</template>
