<script setup>
defineProps({
  source: { type: Object, required: true },
  large: { type: Boolean, default: false },
});
</script>

<template>
  <span
    class="source-logo"
    :class="[`source-logo-${source.accent}`, { 'source-logo-large': large }]"
    aria-hidden="true"
  >
    <img v-if="source.icon" :src="source.icon" alt="">
    <strong v-else>{{ source.short }}</strong>
  </span>
</template>
