<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  images: { type: Array, default: () => [] },
  selectable: { type: Boolean, default: false },
  selected: { type: Array, default: () => [] },
});

const emit = defineEmits(["update:selected"]);
const index = ref(0);
const direction = ref(1);
let touchX = null;

const normalizedImages = computed(
  () => [...new Set(props.images.filter((item) => typeof item === "string" && item))],
);
const activeImage = computed(() => normalizedImages.value[index.value] || "");
const selectedSet = computed(() => new Set(props.selected));

watch(normalizedImages, (images) => {
  if (index.value >= images.length) index.value = 0;
  if (props.selectable && images.length && !props.selected.length) {
    emit("update:selected", images.map((_, itemIndex) => itemIndex));
  }
}, { immediate: true });

function go(nextIndex) {
  const length = normalizedImages.value.length;
  if (!length) return;
  const normalized = (nextIndex + length) % length;
  direction.value = normalized >= index.value ? 1 : -1;
  index.value = normalized;
}

function onKeydown(event) {
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    go(index.value - 1);
  }
  if (event.key === "ArrowRight") {
    event.preventDefault();
    go(index.value + 1);
  }
}

function toggleSelection(itemIndex) {
  const next = new Set(props.selected);
  if (next.has(itemIndex) && next.size > 1) next.delete(itemIndex);
  else next.add(itemIndex);
  emit("update:selected", [...next].sort((left, right) => left - right));
}

function selectAll() {
  emit("update:selected", normalizedImages.value.map((_, itemIndex) => itemIndex));
}

function onTouchStart(event) {
  touchX = event.touches[0]?.clientX ?? null;
}

function onTouchEnd(event) {
  if (touchX === null) return;
  const distance = (event.changedTouches[0]?.clientX ?? touchX) - touchX;
  if (Math.abs(distance) > 44) go(index.value + (distance < 0 ? 1 : -1));
  touchX = null;
}
</script>

<template>
  <section
    v-if="normalizedImages.length"
    class="image-carousel"
    tabindex="0"
    aria-label="Изображения публикации"
    @keydown="onKeydown"
    @touchstart.passive="onTouchStart"
    @touchend.passive="onTouchEnd"
  >
    <div class="image-stage">
      <Transition :name="direction > 0 ? 'image-next' : 'image-prev'" mode="out-in">
        <img
          :key="activeImage"
          :src="activeImage"
          :alt="`Изображение ${index + 1} из ${normalizedImages.length}`"
        >
      </Transition>
      <template v-if="normalizedImages.length > 1">
        <button
          class="image-arrow image-arrow-prev"
          type="button"
          aria-label="Предыдущее изображение"
          @click="go(index - 1)"
        >
          ‹
        </button>
        <button
          class="image-arrow image-arrow-next"
          type="button"
          aria-label="Следующее изображение"
          @click="go(index + 1)"
        >
          ›
        </button>
      </template>
      <span class="image-counter" aria-live="polite">
        {{ index + 1 }} / {{ normalizedImages.length }}
      </span>
    </div>

    <div v-if="selectable" class="image-selection-summary">
      <span>К посту: <strong>{{ selected.length }}</strong> из {{ normalizedImages.length }}</span>
      <button
        class="text-action"
        type="button"
        :disabled="selected.length === normalizedImages.length"
        @click="selectAll"
      >
        Выбрать все
      </button>
    </div>

    <div
      v-if="normalizedImages.length > 1"
      class="thumbnail-rail"
      aria-label="Все изображения"
    >
      <div
        v-for="(image, itemIndex) in normalizedImages"
        :key="image"
        class="thumbnail-item"
        :class="{ 'thumbnail-excluded': selectable && !selectedSet.has(itemIndex) }"
      >
        <button
          type="button"
          :class="{ active: itemIndex === index }"
          :aria-current="itemIndex === index ? 'true' : undefined"
          :aria-label="`Показать изображение ${itemIndex + 1}`"
          @click="go(itemIndex)"
        >
          <img :src="image" alt="" loading="lazy">
        </button>
        <label v-if="selectable" class="thumbnail-toggle">
          <input
            type="checkbox"
            :checked="selectedSet.has(itemIndex)"
            :aria-label="`Прикрепить изображение ${itemIndex + 1}`"
            @change="toggleSelection(itemIndex)"
          >
          <span aria-hidden="true">✓</span>
        </label>
      </div>
    </div>
  </section>
</template>
