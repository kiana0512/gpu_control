import { createApp } from "vue";
import { createPinia } from "pinia";
import { ElIcon } from "element-plus";
import "element-plus/dist/index.css";
import App from "./App.vue";
import { router } from "./router";
import "./styles.css";
import "./resource.css";
import "./liclick-theme.css";
import "./admin-refresh.css";

createApp(App)
  .use(createPinia())
  .use(router)
  .component("ElIcon", ElIcon)
  .mount("#app");
