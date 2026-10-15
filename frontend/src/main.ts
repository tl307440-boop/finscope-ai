import { createPinia } from "pinia"
import { createApp } from "vue"
import ElementPlus from "element-plus"
import zhCn from "element-plus/es/locale/lang/zh-cn"
import "element-plus/dist/index.css"
import "element-plus/theme-chalk/dark/css-vars.css"
import App from "./App.vue"
import { installCharts } from "./charts"
import { router } from "./router"
import { applyTheme, readTheme } from "./shell/theme"
import "./styles/tokens.css"
import "./styles/global.css"

// 先落主题，避免 Element Plus 首屏闪一下亮色。
applyTheme(readTheme(window.localStorage.getItem("finscope-theme")))

const app = createApp(App)
app.use(createPinia())
app.use(router)
// 规格书技术选型：组件库 Element Plus，中文语境。
app.use(ElementPlus, { locale: zhCn })
installCharts(app)
app.mount("#app")
