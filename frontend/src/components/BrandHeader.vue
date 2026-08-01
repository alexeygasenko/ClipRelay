<script setup>
defineProps({
  title: { type: String, default: "" },
  lead: { type: String, default: "" },
  compact: { type: Boolean, default: false },
});
</script>

<template>
  <header class="page-header" :class="{ 'page-header-compact': compact }">
    <div class="brand-row">
      <RouterLink class="brand-title" to="/" aria-label="ClipRelay — на главную">
        ClipRelay
      </RouterLink>
      <div v-if="$slots.actions" class="header-actions">
        <slot name="actions"></slot>
      </div>
    </div>
    <h1 v-if="title">{{ title }}</h1>
    <p v-if="lead" class="page-lead">{{ lead }}</p>
  </header>
</template>
