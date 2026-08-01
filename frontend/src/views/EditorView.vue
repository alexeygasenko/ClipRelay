<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import BrandHeader from "../components/BrandHeader.vue";
import ChannelSelect from "../components/ChannelSelect.vue";
import ImageCarousel from "../components/ImageCarousel.vue";
import TelegramComposer from "../components/TelegramComposer.vue";
import { getJson, pathFromUrl, postForm } from "../api.js";
import { session } from "../session.js";

const props = defineProps({
  kind: { type: String, required: true },
});

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const publishing = ref(false);
const cancelling = ref(false);
const error = ref("");
const payload = ref(null);
const captionHtml = ref("");
const chatId = ref("");
const selectedImages = ref([]);

const endpoints = computed(() => ({
  media: {
    load: `/api/media/jobs/${encodeURIComponent(route.params.id)}`,
    send: `/send/${encodeURIComponent(route.params.id)}`,
    cancel: `/cancel/${encodeURIComponent(route.params.id)}`,
  },
  youtube: {
    load: `/api/youtube/jobs/${encodeURIComponent(route.params.id)}`,
    send: `/youtube/send/${encodeURIComponent(route.params.id)}`,
  },
  spotify: {
    load: `/api/spotify/tracks/${encodeURIComponent(route.params.id)}`,
    send: `/spotify/send/${encodeURIComponent(route.params.id)}`,
  },
})[props.kind]);

const job = computed(() => payload.value?.job || payload.value || {});
const entity = computed(
  () => job.value.video
    || job.value.track
    || job.value.media
    || payload.value?.video
    || payload.value?.track
    || payload.value?.media
    || job.value,
);
const channels = computed(
  () => payload.value?.telegram_channels
    || session.bootstrap?.telegram_channels
    || [],
);
const sourceId = computed(() => {
  if (props.kind === "youtube" || props.kind === "spotify") return props.kind;
  const platform = (entity.value.platform || payload.value?.source || session.lastSource || "tiktok").toLowerCase();
  if (platform === "x") return "twitter";
  return platform;
});
const title = computed(
  () => entity.value.title
    || entity.value.description?.slice(0, 90)
    || entity.value.author
    || entity.value.username
    || "Публикация",
);
const subtitle = computed(
  () => entity.value.artist
    || entity.value.channel
    || entity.value.subreddit
    || (entity.value.username ? `@${entity.value.username.replace(/^@/, "")}` : ""),
);
const authorUrl = computed(
  () => safeLink(entity.value.author_url || entity.value.url || ""),
);
const downloadUrl = computed(
  () => safeLink(
    payload.value?.download_url
      || payload.value?.video_download_url
      || job.value.download_url
      || entity.value.download_url
      || "",
  ),
);
const mediaType = computed(
  () => entity.value.media_type || payload.value?.media_type || (
    props.kind === "spotify" ? "audio" : props.kind === "youtube" ? "image" : "video"
  ),
);
const captionMaxLength = computed(() => (mediaType.value === "text" ? 4096 : 1024));
const captionLength = computed(() => {
  const parsed = new DOMParser().parseFromString(captionHtml.value || "", "text/html");
  return parsed.body.textContent?.length || 0;
});
const captionTooLong = computed(() => captionLength.value > captionMaxLength.value);
const images = computed(() => {
  const values = payload.value?.preview_urls
    || job.value.preview_urls
    || entity.value.preview_urls
    || entity.value.images
    || payload.value?.images
    || [];
  if (values.length) return values.filter(Boolean);
  const preview = payload.value?.preview_url || entity.value.preview_url;
  return mediaType.value === "image" && preview ? [preview] : [];
});
const cover = computed(
  () => entity.value.thumbnail_url
    || entity.value.cover_url
    || payload.value?.thumbnail_url
    || "",
);
const videoUrl = computed(() => {
  if (mediaType.value !== "video") return "";
  return payload.value?.preview_url
    || job.value.preview_url
    || entity.value.preview_url
    || `/preview/${encodeURIComponent(route.params.id)}`;
});
const isTextOnly = computed(
  () => !images.value.length && !cover.value && !videoUrl.value,
);
const editorHeading = computed(() => ({
  media: "Проверьте публикацию",
  youtube: "Подготовьте YouTube-пост",
  spotify: "Подготовьте Spotify-пост",
})[props.kind]);
const editorLead = computed(() => ({
  media: "Выберите медиа, отредактируйте подпись и отправьте всё в Telegram.",
  youtube: "Превью, ссылка на видео и текст будут собраны в один аккуратный пост.",
  spotify: "MP3-трек и подготовленная подпись будут отправлены в выбранный канал.",
})[props.kind]);

function escapeHtml(value) {
  const element = document.createElement("div");
  element.textContent = value || "";
  return element.innerHTML;
}

function safeLink(value) {
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function initialCaption() {
  if (payload.value?.caption_html) return payload.value.caption_html;
  const item = entity.value;
  if (props.kind === "youtube") {
    const url = safeLink(item.url);
    const linkedTitle = url
      ? `<a href="${escapeHtml(url)}">${escapeHtml(item.title || "Смотреть на YouTube")}</a>`
      : escapeHtml(item.title || "Смотреть на YouTube");
    return `<p>${linkedTitle}</p>${item.channel ? `<p>${escapeHtml(item.channel)}</p>` : ""}`;
  }
  if (props.kind === "spotify") {
    const url = safeLink(item.url);
    const linkedTitle = url
      ? `<a href="${escapeHtml(url)}">${escapeHtml(item.title || "Открыть в Spotify")}</a>`
      : escapeHtml(item.title || "Открыть в Spotify");
    return `<p>${linkedTitle}</p>${item.artist ? `<p>${escapeHtml(item.artist)}</p>` : ""}`;
  }
  const username = (item.username || item.author || "").replace(/^@/, "");
  const link = safeLink(item.author_url || item.url);
  const author = username
    ? (link
      ? `<p><a href="${escapeHtml(link)}">@${escapeHtml(username)}</a></p>`
      : `<p>@${escapeHtml(username)}</p>`)
    : "";
  const description = item.description || item.text || item.title || "";
  return `${author}${description ? `<blockquote>${escapeHtml(description)}</blockquote>` : ""}`;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    payload.value = await getJson(endpoints.value.load);
    chatId.value = payload.value.selected_chat_id
      || job.value.selected_chat_id
      || route.query.chat_id
      || channels.value[0]?.chat_id
      || "";
    selectedImages.value = images.value.map((_, index) => index);
    captionHtml.value = initialCaption();
  } catch (loadError) {
    error.value = loadError.message;
  } finally {
    loading.value = false;
  }
}

async function publish() {
  if (captionTooLong.value) {
    error.value = `Сократите текст до ${captionMaxLength.value} символов.`;
    return;
  }
  if (publishing.value || !chatId.value) return;
  publishing.value = true;
  error.value = "";
  try {
    const values = {
      chat_id: chatId.value,
      caption_html: captionHtml.value,
      before_text: "",
      after_text: "",
    };
    if (props.kind === "media") {
      Object.assign(values, {
        caption_options_present: "1",
        include_author: "on",
        include_description: "on",
        image_selection_present: images.value.length ? "1" : "",
        selected_image_indices: selectedImages.value,
      });
    }
    const response = await postForm(endpoints.value.send, values);
    await router.replace(
      response.redirect
        ? pathFromUrl(response.redirect, "/done")
        : { name: "done", query: { source: sourceId.value } },
    );
  } catch (publishError) {
    error.value = publishError.message;
  } finally {
    publishing.value = false;
  }
}

async function cancelPost() {
  if (cancelling.value) return;
  cancelling.value = true;
  try {
    if (endpoints.value.cancel) await postForm(endpoints.value.cancel);
  } catch {
    // The job may already be gone; returning home is still the useful outcome.
  } finally {
    cancelling.value = false;
    await router.replace({ name: "home" });
  }
}

function backToSource() {
  router.push({ name: "home", query: { source: sourceId.value } });
}

watch(() => route.params.id, load);
onMounted(load);
</script>

<template>
  <main class="page-shell editor-page">
    <BrandHeader :title="editorHeading" :lead="editorLead">
      <template #actions>
        <button class="header-pill" type="button" @click="backToSource">← Назад</button>
        <button class="header-pill" type="button" @click="cancelPost">
          {{ cancelling ? "Закрываем…" : "На главную" }}
        </button>
      </template>
    </BrandHeader>

    <div v-if="loading" class="editor-loading">
      <div class="skeleton skeleton-media"></div>
      <div class="skeleton skeleton-form"></div>
    </div>

    <section v-else-if="error && !payload" class="empty-state glass-panel">
      <span class="empty-state-mark" aria-hidden="true">!</span>
      <h2>Не удалось открыть публикацию</h2>
      <p>{{ error }}</p>
      <button class="button button-primary" type="button" @click="load">Повторить</button>
    </section>

    <section v-else class="editor-layout">
      <aside class="editor-media-column">
        <div class="editor-media glass-panel">
          <ImageCarousel
            v-if="images.length"
            v-model:selected="selectedImages"
            :images="images"
            :selectable="images.length > 1"
          />
          <video
            v-else-if="videoUrl"
            :src="videoUrl"
            controls
            playsinline
            preload="metadata"
          ></video>
          <img
            v-else-if="cover"
            :src="cover"
            :alt="title"
            :class="{ 'spotify-cover': props.kind === 'spotify' }"
          >
          <div v-else-if="isTextOnly" class="text-only-media">
            <span aria-hidden="true">Aa</span>
            <strong>Текстовая публикация</strong>
            <p>{{ entity.description || entity.text || title }}</p>
          </div>
        </div>

        <div class="editor-media-meta glass-panel">
          <span class="result-ready-badge">Исходная публикация</span>
          <h2>{{ title }}</h2>
          <p v-if="subtitle">{{ subtitle }}</p>
          <div v-if="downloadUrl || authorUrl" class="editor-media-actions">
            <a
              v-if="downloadUrl"
              class="button button-secondary"
              :href="downloadUrl"
              download
            >
              Скачать
            </a>
            <a
              v-if="authorUrl"
              class="text-action"
              :href="authorUrl"
              target="_blank"
              rel="noreferrer"
            >
              Открыть источник ↗
            </a>
          </div>
        </div>
      </aside>

      <form class="editor-form glass-panel" @submit.prevent="publish">
        <ChannelSelect
          id="editor-chat"
          v-model="chatId"
          :channels="channels"
          label="Куда опубликовать"
        />

        <TelegramComposer
          v-model="captionHtml"
          :max-length="captionMaxLength"
        />

        <div v-if="error" class="inline-alert inline-alert-error" role="alert">
          {{ error }}
        </div>

        <div class="editor-submit-row">
          <span>
            {{ images.length > 1 ? `Выбрано изображений: ${selectedImages.length}` : "Проверьте пост перед отправкой" }}
          </span>
          <button
            class="button button-primary publish-button"
            type="submit"
            :disabled="publishing || !chatId || captionTooLong || (images.length && !selectedImages.length)"
          >
            <span v-if="publishing" class="button-spinner" aria-hidden="true"></span>
            {{ publishing ? "Отправляем в Telegram…" : "Отправить в Telegram" }}
          </button>
        </div>
      </form>
    </section>
  </main>
</template>
