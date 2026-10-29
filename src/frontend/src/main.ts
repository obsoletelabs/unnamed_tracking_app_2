import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";
import { openPluginDialog } from "./state/pluginExtensions";
import { configureNativePluginHost } from "./state/pluginNative";
import { startPwa } from "./services/pwa";
import "./style.css";
import "./styles/ui.css";
import "./styles/tokens.css";
import "./styles/classic.css";
import { initializeUiAppearance } from "./state/uiAppearance";
import { loadBranding } from "./state/branding";

initializeUiAppearance();
void loadBranding();

configureNativePluginHost(router, openPluginDialog);
startPwa(router.isReady());
createApp(App).use(router).mount("#app");
