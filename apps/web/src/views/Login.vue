<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { api, session } from "../api";
const username = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);
const router = useRouter();
async function submit() {
  busy.value = true;
  error.value = "";
  try {
    const result = await api.login(username.value, password.value);
    session.set(result);
    await router.push("/");
  } catch (e) {
    error.value = e instanceof Error ? e.message : "登录失败";
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <main class="login">
    <form @submit.prevent="submit">
      <div class="login-mark">▦</div>
      <h1>GPU Control</h1>
      <p>三节点 ComfyUI 运维控制台</p>
      <label
        >管理员账号<input
          v-model="username"
          autocomplete="username"
          required /></label
      ><label
        >密码<input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
      /></label>
      <div v-if="error" class="form-error">{{ error }}</div>
      <button class="primary" :disabled="busy">
        {{ busy ? "正在登录…" : "登录" }}
      </button>
    </form>
  </main>
</template>
