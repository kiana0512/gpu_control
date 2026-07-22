<script setup lang="ts">
defineProps<{ value: string }>();
</script>
<template>
  <span class="status-mark" :class="value.toLowerCase()"
    ><i></i>{{ value }}</span
  >
</template>
