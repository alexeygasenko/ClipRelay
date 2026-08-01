<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import BrandHeader from "../components/BrandHeader.vue";
import SourceLogo from "../components/SourceLogo.vue";
import SourcePanel from "../components/SourcePanel.vue";
import { editorRouteFromUrl } from "../api.js";
import { enabledSources } from "../sources.js";
import { session } from "../session.js";

const route = useRoute();
const router = useRouter();
const sources = computed(
  () => enabledSources(session.bootstrap?.permissions || {}),
);
const channels = computed(
  () => session.bootstrap?.telegram_channels || [],
);
const initialSource = sources.value.some((item) => item.id === route.query.source)
  ? route.query.source
  : "";
const initialSourceIndex = Math.max(
  0,
  sources.value.findIndex((item) => item.id === initialSource),
);
const selectedId = ref(initialSource);
const layoutMode = ref(initialSource ? "carousel" : "picker");
const transitioning = ref(false);
const pendingFocusId = ref("");
const activePanelHeight = ref(0);
const ringTurn = ref(initialSourceIndex);
const lastRingDirection = ref(1);
const sourceTabElements = new Map();
const sourcePanelElements = new Map();
const sourcePanelHeights = new Map();
let swipeStart = null;
let sourcePanelResizeObserver = null;

const activeIndex = computed(
  () => Math.max(0, sources.value.findIndex((source) => source.id === selectedId.value)),
);
const ringStepDegrees = computed(
  () => (sources.value.length ? 360 / sources.value.length : 0),
);
const ringRotationDegrees = computed(
  () => -ringTurn.value * ringStepDegrees.value,
);
const ringTrackStyle = computed(
  () => ({
    transform: `rotateY(${ringRotationDegrees.value}deg)`,
  }),
);
const farOrbitOffset = computed(
  () => Math.max(64, Math.round(activePanelHeight.value * 1.72 - 170)),
);
const backOrbitOffset = computed(
  () => Math.max(88, Math.round(activePanelHeight.value * 1.96 - 145)),
);

function positiveModulo(value, divisor) {
  return divisor ? ((value % divisor) + divisor) % divisor : 0;
}

function ringIndex() {
  return positiveModulo(ringTurn.value, sources.value.length);
}

function alignRingToIndex(targetIndex, preferredDirection = 0) {
  const count = sources.value.length;
  if (!count || targetIndex < 0) return;
  if (!selectedId.value) {
    ringTurn.value = targetIndex;
    return;
  }

  const currentIndex = ringIndex();
  let delta = positiveModulo(targetIndex - currentIndex, count);
  if (delta > count / 2) delta -= count;
  if (count % 2 === 0 && Math.abs(delta) === count / 2) {
    const direction = preferredDirection || lastRingDirection.value || 1;
    delta = Math.abs(delta) * Math.sign(direction);
  }
  if (delta) {
    lastRingDirection.value = Math.sign(delta);
    ringTurn.value += delta;
  }
}

function alignRingToSource(id, preferredDirection = 0) {
  alignRingToIndex(
    sources.value.findIndex((source) => source.id === id),
    preferredDirection,
  );
}

watch(
  () => route.query.source,
  (source) => {
    const nextId = sources.value.some((item) => item.id === source) ? source : "";
    if (nextId === selectedId.value) return;
    const sceneChanges = Boolean(nextId) !== Boolean(selectedId.value);
    if (nextId) alignRingToSource(nextId);
    transitioning.value = sceneChanges;
    pendingFocusId.value = nextId;
    selectedId.value = nextId;
    if (!sceneChanges && nextId) {
      pendingFocusId.value = "";
      nextTick(() => sourceTabElements.get(nextId)?.focus({ preventScroll: true }));
    }
  },
);

watch(
  () => sources.value.map((source) => source.id).join("|"),
  () => {
    const index = sources.value.findIndex((source) => source.id === selectedId.value);
    ringTurn.value = Math.max(0, index);
  },
);

function ringPositionFor(index) {
  const count = sources.value.length;
  if (!selectedId.value || !count) return "picker";
  const offset = (index - activeIndex.value + count) % count;
  if (offset === 0) return "active";
  if (offset === 1) return "right";
  if (offset === count - 1) return "left";
  if (count > 4 && offset === 2) return "right-far";
  if (count > 4 && offset === count - 2) return "left-far";
  return "back";
}

function ringSlotStyle(index) {
  const slotAngle = index * ringStepDegrees.value;
  return {
    transform: `rotateY(${slotAngle}deg) translateZ(var(--ring-radius))`,
  };
}

function ringFaceAngle(index) {
  const slotAngle = index * ringStepDegrees.value;
  const faceAngle = -(slotAngle + ringRotationDegrees.value);
  return faceAngle;
}

function ringTabFaceStyle(index) {
  return {
    transform: `
      translateY(calc(-50% + var(--orbit-y)))
      rotateY(${ringFaceAngle(index)}deg)
      rotateY(var(--orbit-yaw))
      scale(var(--orbit-scale))
    `,
  };
}

function ringPanelFaceStyle(index) {
  return {
    transform: `
      translateY(var(--orbit-y))
      rotateY(${ringFaceAngle(index)}deg)
      rotateY(var(--orbit-yaw))
      scale(var(--orbit-scale))
    `,
  };
}

async function selectSource(id, animate = true, preferredDirection = 0) {
  if (selectedId.value === id) return;
  const sceneChanges = !selectedId.value;
  alignRingToSource(id, preferredDirection);
  transitioning.value = animate && sceneChanges;
  pendingFocusId.value = transitioning.value ? id : "";
  selectedId.value = id;
  session.lastSource = id;
  await router.replace({ name: "home", query: { source: id } });
}

function setSourceTab(id, element) {
  if (element) sourceTabElements.set(id, element);
  else sourceTabElements.delete(id);
}

function panelHeight(element, entry = null) {
  const borderBox = entry?.borderBoxSize;
  const measured = Array.isArray(borderBox)
    ? borderBox[0]?.blockSize
    : borderBox?.blockSize;
  return Math.ceil(measured || element?.offsetHeight || 0);
}

function updatePanelHeight(id, element, entry = null) {
  const height = panelHeight(element, entry);
  if (!height) return;
  sourcePanelHeights.set(id, height);
  if (id === selectedId.value) activePanelHeight.value = height;
}

function ensurePanelResizeObserver() {
  if (sourcePanelResizeObserver || typeof ResizeObserver === "undefined") {
    return sourcePanelResizeObserver;
  }
  sourcePanelResizeObserver = new ResizeObserver((entries) => {
    entries.forEach((entry) => {
      const id = entry.target.dataset.sourceId;
      if (id) updatePanelHeight(id, entry.target, entry);
    });
  });
  return sourcePanelResizeObserver;
}

function setSourcePanel(id, element) {
  const previous = sourcePanelElements.get(id);
  if (previous && previous !== element) {
    sourcePanelResizeObserver?.unobserve(previous);
  }
  if (!element) {
    sourcePanelElements.delete(id);
    sourcePanelHeights.delete(id);
    return;
  }
  sourcePanelElements.set(id, element);
  updatePanelHeight(id, element);
  ensurePanelResizeObserver()?.observe(element);
}

watch(
  selectedId,
  async (id) => {
    if (!id) return;
    await nextTick();
    const element = sourcePanelElements.get(id);
    if (element) updatePanelHeight(id, element);
  },
  { flush: "post" },
);

onBeforeUnmount(() => {
  sourcePanelResizeObserver?.disconnect();
  sourcePanelElements.clear();
  sourcePanelHeights.clear();
});

function onHomeSceneAfterLeave() {
  layoutMode.value = selectedId.value ? "carousel" : "picker";
}

async function onHomeSceneAfterEnter() {
  transitioning.value = false;
  const focusId = pendingFocusId.value;
  pendingFocusId.value = "";
  if (!focusId || !selectedId.value) return;
  await nextTick();
  sourceTabElements.get(focusId)?.focus({ preventScroll: true });
}

function move(offset) {
  if (!selectedId.value || !sources.value.length) return;
  const next = (activeIndex.value + offset + sources.value.length) % sources.value.length;
  selectSource(sources.value[next].id, false, offset);
}

function onCarouselKeydown(event) {
  if (
    event.target instanceof Element
    && event.target.closest("input, textarea, select, [contenteditable='true']")
  ) return;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    move(-1);
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    move(1);
  }
}

function onPanelTouchStart(event) {
  const target = event.target;
  if (
    target instanceof Element
    && target.closest("input, textarea, select, button, a, [contenteditable='true']")
  ) {
    swipeStart = null;
    return;
  }
  const touch = event.touches[0];
  swipeStart = touch ? { x: touch.clientX, y: touch.clientY } : null;
}

function onPanelTouchEnd(event) {
  if (!swipeStart) return;
  const touch = event.changedTouches[0];
  const start = swipeStart;
  swipeStart = null;
  if (!touch) return;
  const deltaX = touch.clientX - start.x;
  const deltaY = touch.clientY - start.y;
  if (Math.abs(deltaX) < 48 || Math.abs(deltaX) <= Math.abs(deltaY)) return;
  move(deltaX < 0 ? 1 : -1);
}

async function resetHome() {
  transitioning.value = true;
  pendingFocusId.value = "";
  selectedId.value = "";
  session.lastSource = "";
  await router.replace({ name: "home" });
}

function openEditor(url, sourceId) {
  session.lastSource = sourceId;
  router.push(editorRouteFromUrl(url, sourceId));
}
</script>

<template>
  <main
    class="home-shell"
    :class="{
      'home-source-picker': layoutMode === 'picker',
      'home-carousel': layoutMode === 'carousel',
      'home-transitioning': transitioning,
    }"
  >
    <Transition
      name="home-scene"
      mode="out-in"
      @after-leave="onHomeSceneAfterLeave"
      @after-enter="onHomeSceneAfterEnter"
    >
      <section
        v-if="!selectedId"
        key="picker"
        class="source-picker-scene"
        aria-labelledby="source-picker-title"
      >
        <h1 id="source-picker-title" class="brand-title source-picker-brand">ClipRelay</h1>
        <p class="visually-hidden">Выберите источник публикации</p>
        <div v-if="sources.length" class="source-picker-grid">
          <button
            v-for="source in sources"
            :key="source.id"
            class="source-picker-card"
            type="button"
            :aria-label="`Выбрать ${source.name}`"
            @click="selectSource(source.id)"
          >
            <SourceLogo :source="source" large />
            <strong>{{ source.name }}</strong>
            <small>{{ source.description }}</small>
          </button>
        </div>
        <div v-else class="empty-state glass-panel source-picker-empty">
          <span class="empty-state-mark" aria-hidden="true">—</span>
          <h2>Нет доступных источников</h2>
          <p>Обратитесь к администратору, чтобы получить доступ к сервисам.</p>
          <RouterLink
            v-if="session.bootstrap?.user?.is_admin"
            class="button button-primary"
            :to="{
              name: 'admin-user',
              params: { id: session.bootstrap.user.id },
            }"
          >
            Настроить доступ
          </RouterLink>
          <RouterLink v-else class="button button-secondary" to="/settings">
            Открыть настройки
          </RouterLink>
        </div>
      </section>

      <div
        v-else
        key="carousel"
        class="home-carousel-workspace"
      >
        <BrandHeader compact title="" lead="">
          <template #actions>
            <button class="header-pill" type="button" @click="resetHome">
              На главную
            </button>
          </template>
        </BrandHeader>

        <section
          class="source-carousel-scene"
          aria-label="Карусель источников"
          @keydown="onCarouselKeydown"
        >
          <nav
            class="source-carousel-tabs"
            aria-label="Источник публикации"
            role="tablist"
          >
            <button
              class="carousel-edge-zone carousel-edge-left carousel-edge-tabs"
              type="button"
              aria-label="Предыдущий источник"
              aria-hidden="true"
              tabindex="-1"
              @click="move(-1)"
            >
              <span class="visually-hidden">Предыдущий источник</span>
            </button>
            <button
              class="carousel-edge-zone carousel-edge-right carousel-edge-tabs"
              type="button"
              aria-label="Следующий источник"
              aria-hidden="true"
              tabindex="-1"
              @click="move(1)"
            >
              <span class="visually-hidden">Следующий источник</span>
            </button>

            <div class="source-carousel-ring-depth">
              <div class="source-carousel-ring" :style="ringTrackStyle">
                <div
                  v-for="(source, index) in sources"
                  :key="source.id"
                  class="source-carousel-tab-slot"
                  :class="`carousel-${ringPositionFor(index)}`"
                  :style="ringSlotStyle(index)"
                  role="presentation"
                >
                  <button
                    :id="`source-tab-${source.id}`"
                    :ref="(element) => setSourceTab(source.id, element)"
                    class="source-carousel-tab"
                    :class="`carousel-${ringPositionFor(index)}`"
                    :style="ringTabFaceStyle(index)"
                    type="button"
                    role="tab"
                    :aria-selected="source.id === selectedId"
                    :aria-controls="`source-panel-${source.id}`"
                    :aria-label="`Открыть ${source.name}`"
                    :tabindex="source.id === selectedId ? 0 : -1"
                    @click="selectSource(source.id, false)"
                  >
                    <SourceLogo :source="source" />
                    <span>{{ source.name }}</span>
                  </button>
                </div>
              </div>
            </div>
          </nav>

          <div
            class="source-panel-carousel"
            :style="{
              '--active-panel-height': `${activePanelHeight || 282}px`,
              '--far-orbit-y': `${farOrbitOffset}px`,
              '--back-orbit-y': `${backOrbitOffset}px`,
            }"
            @touchstart.passive="onPanelTouchStart"
            @touchend.passive="onPanelTouchEnd"
          >
            <button
              class="carousel-edge-zone carousel-edge-left carousel-edge-panel"
              type="button"
              aria-label="Предыдущий источник"
              aria-hidden="true"
              tabindex="-1"
              @click="move(-1)"
            >
              <span class="visually-hidden">Предыдущий источник</span>
            </button>
            <button
              class="carousel-edge-zone carousel-edge-right carousel-edge-panel"
              type="button"
              aria-label="Следующий источник"
              aria-hidden="true"
              tabindex="-1"
              @click="move(1)"
            >
              <span class="visually-hidden">Следующий источник</span>
            </button>

            <div class="source-panel-ring-depth">
              <div class="source-panel-ring" :style="ringTrackStyle">
                <div
                  v-for="(source, index) in sources"
                  :key="source.id"
                  class="source-panel-orbit-slot"
                  :class="`carousel-${ringPositionFor(index)}`"
                  :style="ringSlotStyle(index)"
                  @click="source.id !== selectedId && selectSource(source.id, false)"
                >
                  <div
                    :id="`source-panel-${source.id}`"
                    :ref="(element) => setSourcePanel(source.id, element)"
                    class="source-panel-position"
                    :class="`carousel-${ringPositionFor(index)}`"
                    :style="ringPanelFaceStyle(index)"
                    :data-source-id="source.id"
                    role="tabpanel"
                    :aria-labelledby="`source-tab-${source.id}`"
                    :aria-hidden="source.id !== selectedId"
                    :inert="source.id !== selectedId"
                  >
                    <SourcePanel
                      :source="source"
                      :channels="channels"
                      :active="source.id === selectedId"
                      :inert="source.id !== selectedId"
                      @open-editor="openEditor"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </Transition>
  </main>
</template>
