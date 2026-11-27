import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { initAnalytics } from "./services/analytics";
import "./styles.css";

void initAnalytics();
createRoot(document.getElementById("root")!).render(<App />);
