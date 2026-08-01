<script setup>
defineProps({
  name: {
    type: String,
    required: true,
    validator: (value) => ["up", "down", "trash"].includes(value),
  },
});
</script>

<template>
  <svg
    v-if="name === 'up'"
    class="action-icon"
    viewBox="0 0 24 24"
    fill="none"
    aria-hidden="true"
  >
    <path d="M12 19V5M6.5 10.5 12 5l5.5 5.5" />
  </svg>
  <svg
    v-else-if="name === 'down'"
    class="action-icon"
    viewBox="0 0 24 24"
    fill="none"
    aria-hidden="true"
  >
    <path d="M12 5v14m5.5-5.5L12 19l-5.5-5.5" />
  </svg>
  <svg
    v-else
    class="action-icon"
    viewBox="0 0 24 24"
    fill="none"
    aria-hidden="true"
  >
    <path d="M4.5 7h15M9 7V4.75h6V7m2.5 0-.65 12.25H7.15L6.5 7M10 10.5v5.5m4-5.5v5.5" />
  </svg>
</template>
