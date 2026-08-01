<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ActionIcon from "../components/ActionIcon.vue";
import BrandHeader from "../components/BrandHeader.vue";
import SourceLogo from "../components/SourceLogo.vue";
import { getJson, postForm } from "../api.js";
import { session } from "../session.js";
import { sourceById } from "../sources.js";

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const error = ref("");
const data = ref(null);
const activeSection = ref("telegram");
const busy = ref("");
const notice = ref("");
const channelSearch = ref("");
const discoverToken = ref("");
const manual = ref({ name: "", chat_id: "", bot_token: "" });
const monitorChannel = ref("");
const interval = ref(300);
const revealDiscoverToken = ref(false);
const revealManualToken = ref(false);

const userId = computed(() => route.query.user_id || "");
const permissions = computed(() => data.value?.permissions || {});
const channels = computed(() => data.value?.telegram_channels || []);
const monitoredChannels = computed(() => data.value?.monitored_tiktok_channels || []);
const cookieServices = computed(() => {
  const services = data.value?.cookie_services || [];
  return services.map((service) => (
    typeof service === "string"
      ? { id: service, name: service[0].toUpperCase() + service.slice(1) }
      : service
  ));
});
const readyCookieCount = computed(
  () => cookieServices.value.filter((service) => service.cookies?.valid).length,
);
const sections = computed(() => [
  {
    id: "telegram",
    label: "Telegram-каналы",
    description: `${channels.value.length} подключено`,
  },
  ...(permissions.value.tiktok !== false
    ? [{
      id: "monitoring",
      label: "Мониторинг TikTok",
      description: `${monitoredChannels.value.length} отслеживается`,
    }]
    : []),
  {
    id: "cookies",
    label: "Cookies сервисов",
    description: `${readyCookieCount.value} из ${cookieServices.value.length} готовы`,
  },
]);
const filteredChannels = computed(() => {
  const query = channelSearch.value.trim().toLowerCase();
  if (!query) return channels.value;
  return channels.value.filter((channel) => (
    `${channel.name} ${channel.chat_id}`.toLowerCase().includes(query)
  ));
});

function mutationValues(values = {}) {
  if (values instanceof FormData) return values;
  return userId.value
    ? { ...values, settings_user_id: userId.value }
    : values;
}

function syncBootstrapChannels(settings) {
  if (!session.bootstrap || !settings || settings.admin_mode) return;
  session.bootstrap = {
    ...session.bootstrap,
    telegram_channels: [...(settings.telegram_channels || [])],
  };
}

async function load(silent = false) {
  if (!silent) loading.value = true;
  error.value = "";
  try {
    const query = userId.value ? `?user_id=${encodeURIComponent(userId.value)}` : "";
    data.value = await getJson(`/api/settings${query}`);
    syncBootstrapChannels(data.value);
    interval.value = data.value.poll_interval_seconds || 300;
    if (!sections.value.some((section) => section.id === activeSection.value)) {
      activeSection.value = sections.value[0]?.id || "telegram";
    }
  } catch (loadError) {
    error.value = loadError.message;
  } finally {
    if (!silent) loading.value = false;
  }
}

function showNotice(message) {
  notice.value = message;
  window.setTimeout(() => {
    if (notice.value === message) notice.value = "";
  }, 3200);
}

async function mutate(key, url, values, successMessage, reload = true) {
  if (busy.value) return null;
  busy.value = key;
  error.value = "";
  try {
    const result = await postForm(url, mutationValues(values));
    syncBootstrapChannels(result.settings);
    if (reload) await load(true);
    showNotice(result.message || successMessage);
    return result;
  } catch (mutationError) {
    error.value = mutationError.message;
    return null;
  } finally {
    busy.value = "";
  }
}

async function discoverChannels() {
  const result = await mutate(
    "discover",
    "/settings/telegram/discover",
    { bot_token: discoverToken.value },
    "Каналы и чаты обновлены.",
  );
  if (result) discoverToken.value = "";
}

async function addChannel() {
  const result = await mutate(
    "add-channel",
    "/settings/telegram",
    manual.value,
    "Telegram-канал добавлен.",
  );
  if (result) manual.value = { name: "", chat_id: "", bot_token: "" };
}

async function deleteChannel(channel) {
  if (!window.confirm(`Удалить «${channel.name}» из ClipRelay?`)) return;
  await mutate(
    `delete-${channel.chat_id}`,
    "/settings/telegram/delete",
    { chat_id: channel.chat_id },
    "Канал удалён.",
  );
}

async function moveChannel(channel, direction) {
  const currentIndex = channels.value.findIndex((item) => item.chat_id === channel.chat_id);
  const nextIndex = currentIndex + (direction === "up" ? -1 : 1);
  if (currentIndex < 0 || nextIndex < 0 || nextIndex >= channels.value.length) return;
  const previous = [...channels.value];
  const next = [...channels.value];
  [next[currentIndex], next[nextIndex]] = [next[nextIndex], next[currentIndex]];
  data.value.telegram_channels = next;
  const result = await mutate(
    `move-${channel.chat_id}`,
    "/settings/telegram/move",
    { chat_id: channel.chat_id, direction },
    "Порядок каналов обновлён.",
    false,
  );
  if (!result) data.value.telegram_channels = previous;
}

async function addMonitor() {
  const result = await mutate(
    "add-monitor",
    "/settings/tiktok/monitor",
    { channel: monitorChannel.value },
    "TikTok-канал добавлен в мониторинг.",
  );
  if (result) monitorChannel.value = "";
}

function deleteMonitor(channel) {
  return mutate(
    `monitor-${channel}`,
    "/settings/tiktok/monitor/delete",
    { channel },
    "Канал удалён из мониторинга.",
  );
}

function saveInterval() {
  return mutate(
    "interval",
    "/settings/tiktok/interval",
    { poll_interval_seconds: interval.value },
    "Интервал проверки сохранён.",
  );
}

async function uploadCookies(service, event) {
  const file = event.target.files?.[0];
  if (!file || busy.value) return;
  const formData = new FormData();
  formData.append("cookies_file", file);
  if (userId.value) formData.append("settings_user_id", userId.value);
  const result = await mutate(
    `cookies-${service.id}`,
    `/settings/cookies/${encodeURIComponent(service.id)}`,
    formData,
    `${service.name}: cookies обновлены.`,
  );
  if (result) event.target.value = "";
}

function cookieStatusTone(service) {
  if (service.cookies?.valid) return "ready";
  return service.cookies?.uploaded ? "warning" : "missing";
}

function cookieStatusLabel(service) {
  if (service.cookies?.valid) return "Загружены и готовы";
  return service.cookies?.uploaded ? "Нужно обновить" : "Не загружены";
}

function cookieStatusHint(service) {
  const status = service.cookies || {};
  if (status.valid) {
    const count = Number(status.cookie_count || 0);
    return count
      ? `Активных cookie: ${count}. Формат и срок действия проверены.`
      : "Формат и срок действия проверены.";
  }
  const hints = {
    invalid_format: "Файл не распознан как cookies.txt в Netscape-формате.",
    empty: "В файле нет активных cookie.",
    expired: "Все cookie в файле истекли.",
    no_active_cookies: "В файле нет активных cookie.",
    missing_auth_cookie: "Не найдена активная cookie авторизации.",
    read_error: "Файл не удалось прочитать.",
  };
  return status.uploaded
    ? (hints[status.reason] || "Файл загружен, но не прошёл локальную проверку.")
    : "Файл для этого сервиса ещё не загружен.";
}

watch(userId, () => load());
onMounted(load);
</script>

<template>
  <main class="page-shell settings-page">
    <BrandHeader
      :title="data?.admin_mode ? `Настройки: ${data.settings_user?.username}` : 'Настройки'"
      lead="Каналы назначения, автоматический мониторинг и доступ к сервисам."
    >
      <template #actions>
        <button
          v-if="data?.admin_mode"
          class="header-pill"
          type="button"
          @click="router.push(`/admin/users/${data.settings_user?.id}`)"
        >
          К пользователю
        </button>
        <RouterLink class="header-pill" to="/">На главную</RouterLink>
      </template>
    </BrandHeader>

    <div v-if="notice" class="toast-notice" role="status">
      <span aria-hidden="true">✓</span>{{ notice }}
    </div>

    <div v-if="loading" class="settings-loading">
      <div class="skeleton settings-nav-skeleton"></div>
      <div class="skeleton settings-content-skeleton"></div>
    </div>

    <section v-else-if="error && !data" class="empty-state glass-panel">
      <span class="empty-state-mark" aria-hidden="true">!</span>
      <h2>Не удалось загрузить настройки</h2>
      <p>{{ error }}</p>
      <button class="button button-primary" type="button" @click="load">Повторить</button>
    </section>

    <div v-else class="settings-layout">
      <aside class="settings-navigation glass-panel" aria-label="Разделы настроек">
        <button
          v-for="section in sections"
          :key="section.id"
          type="button"
          :class="{ active: activeSection === section.id }"
          :aria-current="activeSection === section.id ? 'page' : undefined"
          @click="activeSection = section.id"
        >
          <span>{{ section.label }}</span>
          <small>{{ section.description }}</small>
        </button>
      </aside>

      <div class="settings-mobile-navigation">
        <label for="settings-section">Раздел настроек</label>
        <div class="select-shell">
          <select id="settings-section" v-model="activeSection">
            <option v-for="section in sections" :key="section.id" :value="section.id">
              {{ section.label }}
            </option>
          </select>
          <span class="select-chevron" aria-hidden="true"></span>
        </div>
      </div>

      <section class="settings-content glass-panel">
        <div v-if="error" class="inline-alert inline-alert-error" role="alert">
          {{ error }}
        </div>

        <Transition name="settings-section" mode="out-in">
          <div v-if="activeSection === 'telegram'" key="telegram" class="settings-section-view">
            <div class="section-heading">
              <div>
                <span class="section-kicker">Доставка публикаций</span>
                <h2>Telegram-каналы и чаты</h2>
                <p>Подключите бота один раз, затем выбирайте назначение на карточке источника.</p>
              </div>
              <span class="section-count">{{ channels.length }}</span>
            </div>

            <div class="settings-form-grid">
              <form
                class="settings-form-card"
                autocomplete="off"
                @submit.prevent="discoverChannels"
              >
                <span class="form-card-step">Быстрое подключение</span>
                <h3>Найти доступные каналы</h3>
                <p>Добавьте бота администратором в Telegram, затем вставьте его токен.</p>
                <label for="discover-token">Токен Telegram-бота</label>
                <div class="secret-field">
                  <input
                    id="discover-token"
                    v-model="discoverToken"
                    :type="revealDiscoverToken ? 'text' : 'password'"
                    name="telegram_discovery_token"
                    autocomplete="new-password"
                    data-1p-ignore
                    data-lpignore="true"
                    placeholder="123456789:bot_token"
                    required
                  >
                  <button
                    type="button"
                    :aria-label="revealDiscoverToken ? 'Скрыть токен' : 'Показать токен'"
                    @click="revealDiscoverToken = !revealDiscoverToken"
                  >
                    {{ revealDiscoverToken ? "Скрыть" : "Показать" }}
                  </button>
                </div>
                <button
                  class="button button-primary"
                  type="submit"
                  :disabled="busy === 'discover'"
                >
                  {{ busy === "discover" ? "Ищем…" : "Найти каналы и чаты" }}
                </button>
              </form>

              <form
                class="settings-form-card"
                autocomplete="off"
                @submit.prevent="addChannel"
              >
                <span class="form-card-step">Ручная настройка</span>
                <h3>Добавить по тегу или ID</h3>
                <p>Подходит для приватного чата или канала, который не появился автоматически.</p>
                <div class="field-pair">
                  <div>
                    <label for="manual-name">Название</label>
                    <input
                      id="manual-name"
                      v-model="manual.name"
                      name="telegram_channel_label"
                      autocomplete="off"
                      placeholder="Рабочий чат"
                      required
                    >
                  </div>
                  <div>
                    <label for="manual-id">Тег или ID</label>
                    <input
                      id="manual-id"
                      v-model="manual.chat_id"
                      name="telegram_chat_identifier"
                      autocomplete="off"
                      placeholder="@channel или -100…"
                      required
                    >
                  </div>
                </div>
                <label for="manual-token">Токен Telegram-бота</label>
                <div class="secret-field">
                  <input
                    id="manual-token"
                    v-model="manual.bot_token"
                    :type="revealManualToken ? 'text' : 'password'"
                    name="telegram_bot_token"
                    autocomplete="new-password"
                    data-1p-ignore
                    data-lpignore="true"
                    placeholder="123456789:bot_token"
                    required
                  >
                  <button
                    type="button"
                    :aria-label="revealManualToken ? 'Скрыть токен' : 'Показать токен'"
                    @click="revealManualToken = !revealManualToken"
                  >
                    {{ revealManualToken ? "Скрыть" : "Показать" }}
                  </button>
                </div>
                <button
                  class="button button-secondary"
                  type="submit"
                  :disabled="busy === 'add-channel'"
                >
                  {{ busy === "add-channel" ? "Добавляем…" : "Добавить вручную" }}
                </button>
              </form>
            </div>

            <div class="saved-destinations">
              <div class="saved-heading">
                <div>
                  <h3>Подключённые назначения</h3>
                  <p>Порядок здесь совпадает с порядком в выборе канала.</p>
                </div>
                <input
                  v-model="channelSearch"
                  class="compact-search"
                  type="search"
                  autocomplete="off"
                  placeholder="Найти канал"
                  aria-label="Поиск по каналам"
                >
              </div>

              <div v-if="filteredChannels.length" class="destination-list">
                <article
                  v-for="(channel, index) in filteredChannels"
                  :key="channel.chat_id"
                  class="destination-row"
                  :class="{ 'row-busy': busy.includes(channel.chat_id) }"
                >
                  <span class="destination-avatar" aria-hidden="true">
                    {{ channel.name.slice(0, 1).toUpperCase() }}
                  </span>
                  <div class="destination-info">
                    <strong>{{ channel.name }}</strong>
                    <small>
                      {{ channel.display }} · {{ channel.kind_label }}
                    </small>
                  </div>
                  <div class="row-actions">
                    <button
                      type="button"
                      aria-label="Поднять выше"
                      :disabled="channels.findIndex((item) => item.chat_id === channel.chat_id) === 0 || !!busy"
                      @click="moveChannel(channel, 'up')"
                    >
                      <ActionIcon name="up" />
                    </button>
                    <button
                      type="button"
                      aria-label="Опустить ниже"
                      :disabled="channels.findIndex((item) => item.chat_id === channel.chat_id) === channels.length - 1 || !!busy"
                      @click="moveChannel(channel, 'down')"
                    >
                      <ActionIcon name="down" />
                    </button>
                    <button
                      class="danger-action"
                      type="button"
                      :aria-label="`Удалить ${channel.name}`"
                      :disabled="!!busy"
                      @click="deleteChannel(channel)"
                    >
                      <ActionIcon name="trash" />
                    </button>
                  </div>
                </article>
              </div>
              <div v-else class="list-empty-state">
                {{ channels.length ? "По этому запросу ничего не найдено." : "Каналы пока не подключены." }}
              </div>
            </div>
          </div>

          <div v-else-if="activeSection === 'monitoring'" key="monitoring" class="settings-section-view">
            <div class="section-heading">
              <div>
                <span class="section-kicker">Автоматизация</span>
                <h2>Мониторинг TikTok</h2>
                <p>ClipRelay проверяет каналы и отправляет новые ролики без ручного запуска.</p>
              </div>
              <span class="section-count">{{ monitoredChannels.length }}</span>
            </div>

            <div class="settings-form-grid settings-form-grid-balanced">
              <form class="settings-form-card" @submit.prevent="addMonitor">
                <span class="form-card-step">Новый канал</span>
                <h3>Добавить в мониторинг</h3>
                <label for="monitor-channel">Имя или ссылка на TikTok-канал</label>
                <input
                  id="monitor-channel"
                  v-model="monitorChannel"
                  autocomplete="off"
                  placeholder="@username или ссылка"
                  required
                >
                <button
                  class="button button-primary"
                  type="submit"
                  :disabled="busy === 'add-monitor'"
                >
                  {{ busy === "add-monitor" ? "Добавляем…" : "Добавить канал" }}
                </button>
              </form>

              <form class="settings-form-card" @submit.prevent="saveInterval">
                <span class="form-card-step">Частота проверки</span>
                <h3>Интервал мониторинга</h3>
                <label for="poll-interval">Секунд между проверками</label>
                <input
                  id="poll-interval"
                  v-model.number="interval"
                  type="number"
                  min="30"
                  step="10"
                  required
                >
                <p class="field-help">Минимум 30 секунд. Для большого числа каналов лучше 120–300.</p>
                <button
                  class="button button-secondary"
                  type="submit"
                  :disabled="busy === 'interval'"
                >
                  {{ busy === "interval" ? "Сохраняем…" : "Сохранить интервал" }}
                </button>
              </form>
            </div>

            <div class="saved-destinations">
              <div class="saved-heading">
                <div>
                  <h3>Отслеживаемые каналы</h3>
                  <p>Новые ролики публикуются в канал по умолчанию.</p>
                </div>
              </div>
              <div v-if="monitoredChannels.length" class="destination-list">
                <article v-for="channel in monitoredChannels" :key="channel" class="destination-row">
                  <span class="destination-avatar tiktok-avatar" aria-hidden="true">TT</span>
                  <div class="destination-info"><strong>@{{ channel.replace(/^@/, "") }}</strong></div>
                  <div class="row-actions">
                    <button
                      class="danger-action"
                      type="button"
                      :aria-label="`Удалить @${channel.replace(/^@/, '')}`"
                      :disabled="!!busy"
                      @click="deleteMonitor(channel)"
                    >
                      <ActionIcon name="trash" />
                    </button>
                  </div>
                </article>
              </div>
              <div v-else class="list-empty-state">Постоянный мониторинг пока не настроен.</div>
            </div>
          </div>

          <div v-else key="cookies" class="settings-section-view">
            <div class="section-heading">
              <div>
                <span class="section-kicker">Доступ к медиа</span>
                <h2>Cookies сервисов</h2>
                <p>Файлы нужны только для контента, который сервис не отдаёт без авторизации.</p>
              </div>
              <span class="section-count">{{ readyCookieCount }}/{{ cookieServices.length }}</span>
            </div>

            <div class="cookies-notice">
              <strong>Безопасная загрузка</strong>
              <p>Используйте cookies.txt в Netscape-формате. Содержимое файла не показывается в интерфейсе.</p>
            </div>

            <div class="cookie-service-grid">
              <article
                v-for="service in cookieServices"
                :key="service.id"
                class="cookie-service-card"
                :class="{ 'cookie-service-card-ready': service.cookies?.valid }"
              >
                <SourceLogo
                  class="cookie-service-mark"
                  :source="sourceById(service.id)"
                />
                <div class="cookie-service-content">
                  <div class="cookie-service-heading">
                    <h3>{{ service.name }}</h3>
                    <span
                      class="cookie-status"
                      :class="`cookie-status-${cookieStatusTone(service)}`"
                    >
                      <i aria-hidden="true"></i>
                      {{ cookieStatusLabel(service) }}
                    </span>
                  </div>
                  <p class="cookie-status-hint">{{ cookieStatusHint(service) }}</p>
                  <p v-if="service.id === 'spotify'">
                    Нужен Premium-аккаунт и cookie <code>sp_dc</code>.
                  </p>
                  <p v-else>
                    При проблемах с загрузкой замените файл свежим экспортом cookies.txt.
                  </p>
                </div>
                <label class="file-button">
                  <input
                    type="file"
                    accept=".txt,text/plain"
                    :disabled="!!busy"
                    @change="uploadCookies(service, $event)"
                  >
                  <span>
                    {{
                      busy === `cookies-${service.id}`
                        ? "Загружаем…"
                        : service.cookies?.uploaded
                          ? "Заменить cookies.txt"
                          : "Выбрать cookies.txt"
                    }}
                  </span>
                </label>
              </article>
            </div>
          </div>
        </Transition>
      </section>
    </div>
  </main>
</template>
