<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { postForm } from "../api.js";
import ChannelSelect from "./ChannelSelect.vue";
import ImageCarousel from "./ImageCarousel.vue";
import SourceLogo from "./SourceLogo.vue";

const props = defineProps({
  source: { type: Object, required: true },
  channels: { type: Array, default: () => [] },
  active: { type: Boolean, default: false },
});

const emit = defineEmits(["open-editor"]);
const mode = ref("single");
const url = ref("");
const chatId = ref(props.channels[0]?.chat_id || "");
const state = ref("idle");
const message = ref("");
const result = ref(null);
const importing = ref(false);
const importExisting = ref(false);
const importResult = ref(null);
let timer = null;
let controller = null;
let requestSequence = 0;

watch(
  () => props.channels,
  (channels) => {
    if (!channels.some((channel) => channel.chat_id === chatId.value)) {
      chatId.value = channels[0]?.chat_id || "";
    }
  },
  { deep: true },
);

watch([url, chatId, () => props.active, mode], () => {
  window.clearTimeout(timer);
  if (!props.active || mode.value !== "single") return;
  if (!url.value.trim()) {
    controller?.abort();
    state.value = "idle";
    message.value = "";
    result.value = null;
    return;
  }
  timer = window.setTimeout(inspect, 480);
});

onBeforeUnmount(() => {
  window.clearTimeout(timer);
  controller?.abort();
});

const previewImages = computed(() => {
  const payload = result.value || {};
  const entity = resultEntity.value;
  const values = [
    payload.preview_urls,
    entity.preview_urls,
    payload.image_urls,
    entity.image_urls,
    payload.images,
    entity.images,
  ].find((items) => Array.isArray(items) && items.length);
  if (values) return values.filter(Boolean);
  const preview = payload.preview_url || entity.preview_url;
  return resultMediaType.value === "image" && preview ? [preview] : [];
});

const resultEntity = computed(() => {
  const payload = result.value || {};
  return payload.post
    || payload.media
    || payload.video
    || payload.track
    || payload;
});

const resultMediaType = computed(
  () => result.value?.media_type || resultEntity.value.media_type || "",
);

const previewVideo = computed(() => {
  const payload = result.value || {};
  if (resultMediaType.value === "image" || previewImages.value.length) return "";
  return payload.preview_url
    || payload.video_preview_url
    || resultEntity.value.preview_url
    || "";
});

const previewCover = computed(
  () => result.value?.thumbnail_url
    || result.value?.cover_url
    || resultEntity.value.thumbnail_url
    || resultEntity.value.cover_url
    || (resultMediaType.value !== "image"
      ? result.value?.preview_image_url || resultEntity.value.preview_image_url
      : ""),
);

const resultTitle = computed(
  () => resultEntity.value.title
    || resultEntity.value.author
    || resultEntity.value.username
    || (props.source.id === "reddit" ? "Публикация Reddit" : props.source.name),
);

const resultMeta = computed(
  () => resultEntity.value.artist
    || resultEntity.value.channel
    || resultEntity.value.subreddit
    || resultEntity.value.description
    || "",
);

function safeAssetUrl(value) {
  if (!value) return "";
  try {
    const urlValue = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(urlValue.protocol) ? urlValue.href : "";
  } catch {
    return "";
  }
}

const downloadLinks = computed(() => {
  const payload = result.value || {};
  const links = [];
  if (payload.thumbnail_download_url) {
    links.push({ label: "Скачать превью", url: payload.thumbnail_download_url });
  }
  if (payload.video_download_url) {
    links.push({
      label: resultMediaType.value === "text"
        ? "Скачать пост · TXT"
        : resultMediaType.value === "image"
          ? `Скачать изображения${payload.image_count ? ` · ${payload.image_count}` : ""}`
          : "Скачать видео",
      url: payload.video_download_url,
    });
  }
  if (payload.audio_download_url) {
    links.push({ label: "Скачать MP3", url: payload.audio_download_url });
  }
  const primaryDownload = payload.download_url || resultEntity.value.download_url;
  if (primaryDownload && !links.some((link) => link.url === primaryDownload)) {
    const label = resultMediaType.value === "text"
      ? "Скачать пост · TXT"
      : resultMediaType.value === "image"
        ? "Скачать изображения"
        : "Скачать медиа";
    links.push({ label, url: primaryDownload });
  }
  return links
    .map((link) => ({ ...link, url: safeAssetUrl(link.url) }))
    .filter((link) => link.url);
});

const postUrl = computed(
  () => result.value?.post_url || result.value?.editor_url || "",
);

async function inspect() {
  const value = url.value.trim();
  if (!value) return;
  controller?.abort();
  controller = new AbortController();
  const sequence = ++requestSequence;
  state.value = "loading";
  message.value = "Получаем публикацию и готовим предпросмотр…";
  result.value = null;
  try {
    const payload = {
      [props.source.parameter]: value,
      chat_id: chatId.value,
    };
    const data = await postForm(props.source.endpoint, payload, {
      signal: controller.signal,
    });
    if (sequence !== requestSequence) return;
    result.value = data;
    state.value = "success";
    message.value = "";
  } catch (error) {
    if (error.name === "AbortError") return;
    if (sequence !== requestSequence) return;
    state.value = "error";
    message.value = error.message;
  }
}

async function importChannel() {
  if (importing.value || !url.value.trim()) return;
  importing.value = true;
  state.value = "loading";
  message.value = "Импортируем канал и настраиваем мониторинг…";
  importResult.value = null;
  try {
    const payload = await postForm("/tiktok/prepare", {
      tiktok_url: url.value.trim(),
      chat_id: chatId.value,
      post_existing: importExisting.value ? "on" : "",
    });
    importResult.value = payload;
    state.value = "success";
    message.value = payload.message || "Канал импортирован. Новые публикации будут отслеживаться.";
  } catch (error) {
    state.value = "error";
    message.value = error.message;
  } finally {
    importing.value = false;
  }
}
</script>

<template>
  <section
    class="source-panel glass-panel"
    :class="{ 'source-panel-active': active }"
    :aria-labelledby="`source-${source.id}-title`"
  >
    <div class="source-panel-heading">
      <SourceLogo :source="source" />
      <div>
        <h2 :id="`source-${source.id}-title`">{{ source.name }}</h2>
        <p>{{ source.description }}</p>
      </div>
    </div>

    <div
      v-if="source.modes"
      class="segmented-control"
      role="tablist"
      aria-label="Режим TikTok"
    >
      <button
        id="tiktok-mode-single"
        type="button"
        role="tab"
        :aria-selected="mode === 'single'"
        aria-controls="tiktok-single-panel"
        :class="{ active: mode === 'single' }"
        @click="mode = 'single'"
      >
        Отдельный пост
      </button>
      <button
        id="tiktok-mode-channel"
        type="button"
        role="tab"
        :aria-selected="mode === 'channel'"
        aria-controls="tiktok-channel-panel"
        :class="{ active: mode === 'channel' }"
        @click="mode = 'channel'"
      >
        Импорт канала
      </button>
    </div>

    <Transition name="state-fade" mode="out-in">
      <div
        v-if="mode === 'single'"
        id="tiktok-single-panel"
        key="single"
        role="tabpanel"
        :aria-labelledby="source.modes ? 'tiktok-mode-single' : undefined"
        class="source-form-layout"
      >
        <div class="source-fields">
          <div class="field-group">
            <label :for="`${source.id}-url`">{{ source.label }}</label>
            <input
              :id="`${source.id}-url`"
              v-model="url"
              type="url"
              inputmode="url"
              autocomplete="off"
              :placeholder="source.placeholder"
              @keydown.enter.prevent="inspect"
            >
          </div>
          <ChannelSelect
            v-if="source.needsChannel"
            :id="`${source.id}-chat`"
            v-model="chatId"
            :channels="channels"
          />
        </div>

        <Transition name="state-fade">
          <div
            v-if="message"
            class="source-status"
            :class="[`source-status-${state}`]"
            role="status"
            aria-live="polite"
          >
            <span class="status-indicator" aria-hidden="true"></span>
            <span>{{ message }}</span>
          </div>
        </Transition>

        <Transition name="result-reveal">
          <article
            v-if="result"
            class="media-result-card"
            :class="{ 'media-result-card-spotify': source.id === 'spotify' }"
          >
            <div class="media-result-preview">
              <ImageCarousel v-if="previewImages.length" :images="previewImages" />
              <video
                v-else-if="previewVideo"
                :src="previewVideo"
                controls
                playsinline
                preload="metadata"
              ></video>
              <img
                v-else-if="previewCover"
                :src="previewCover"
                :alt="resultTitle"
                :class="{ 'spotify-cover': source.id === 'spotify' }"
              >
              <div v-else class="text-post-preview">
                <span aria-hidden="true">Aa</span>
                <strong>Текстовая публикация</strong>
              </div>
            </div>
            <div class="media-result-content">
              <span class="result-ready-badge">Готово к публикации</span>
              <h3>{{ resultTitle }}</h3>
              <p v-if="resultMeta">{{ resultMeta }}</p>
              <div class="result-actions">
                <a
                  v-for="link in downloadLinks"
                  :key="link.url"
                  class="button button-secondary"
                  :href="link.url"
                  download
                >
                  {{ link.label }}
                </a>
                <button
                  v-if="postUrl"
                  class="button button-primary"
                  type="button"
                  @click="emit('open-editor', postUrl, source.id)"
                >
                  Подготовить пост
                </button>
              </div>
            </div>
          </article>
        </Transition>
      </div>

      <form
        v-else
        id="tiktok-channel-panel"
        key="channel"
        role="tabpanel"
        aria-labelledby="tiktok-mode-channel"
        class="channel-import-panel"
        @submit.prevent="importChannel"
      >
        <div class="field-group">
          <label for="tiktok-channel-url">Ссылка на TikTok-канал</label>
          <input
            id="tiktok-channel-url"
            v-model="url"
            type="url"
            inputmode="url"
            autocomplete="off"
            placeholder="https://www.tiktok.com/@username"
            required
          >
        </div>
        <ChannelSelect
          id="tiktok-import-chat"
          v-model="chatId"
          :channels="channels"
        />
        <label class="choice-card">
          <input v-model="importExisting" type="checkbox">
          <span>
            <strong>Опубликовать существующие видео</strong>
            <small>Если выключено, текущие ролики будут пропущены, а новые — отслеживаться.</small>
          </span>
        </label>
        <div
          v-if="message"
          class="source-status"
          :class="[`source-status-${state}`]"
          role="status"
        >
          <span class="status-indicator" aria-hidden="true"></span>
          <span>{{ message }}</span>
        </div>
        <button
          class="button button-primary import-submit"
          type="submit"
          :disabled="importing || !channels.length"
        >
          {{ importing ? "Импортируем…" : "Импортировать канал" }}
        </button>
      </form>
    </Transition>
  </section>
</template>
