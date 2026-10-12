import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import {
  Map as LibreMap,
  Marker,
  Popup,
  LngLatBounds,
  AttributionControl,
  setWorkerUrl,
} from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Candidate, ProjectLocation } from "../domain/models";
import { priorities, projectPriority } from "../domain/geography";
import { StatusBadge } from "./ui";

setWorkerUrl(workerUrl);
export const MAP_STYLE =
  import.meta.env.VITE_MAP_STYLE_URL ||
  "https://tiles.openfreemap.org/styles/positron";
export type MapProject = { candidate: Candidate; location: ProjectLocation };
const DEFAULT_CENTER: [number, number] = [61.15, 56.85];

export function ProjectMap({
  projects,
  reset,
  onLoading,
  onError,
}: {
  projects: MapProject[];
  reset: number;
  onLoading: (value: boolean) => void;
  onError: (message: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const popupRef = useRef<Popup | null>(null);
  const [map, setMap] = useState<LibreMap | null>(null);
  const [selection, setSelection] = useState<{
    project: MapProject;
    host: HTMLDivElement;
  } | null>(null);

  useEffect(() => {
    if (!container.current) return;
    onLoading(true);
    onError(null);
    let instance: LibreMap;
    try {
      instance = new LibreMap({
        container: container.current,
        style: MAP_STYLE,
        center: DEFAULT_CENTER,
        zoom: 8,
        minZoom: 3,
        maxZoom: 18,
        attributionControl: false,
        renderWorldCopies: false,
        dragRotate: false,
        pitchWithRotate: false,
        fadeDuration: 0,
        locale: {
          "AttributionControl.ToggleAttribution": "Показать авторство карты",
        },
      });
    } catch {
      onLoading(false);
      onError(
        "Не удалось запустить карту. Проверьте поддержку WebGL в браузере. Список проектов доступен ниже.",
      );
      return;
    }
    instance.scrollZoom.disable();
    instance.touchZoomRotate.disableRotation();
    instance.addControl(
      new AttributionControl({
        compact: false,
      }),
      "bottom-right",
    );
    instance
      .getCanvas()
      .setAttribute(
        "aria-label",
        "Карта инвестиционных проектов. Стрелки — перемещение, плюс и минус — масштаб.",
      );
    const loaded = () => onLoading(false);
    const failed = () => {
      onLoading(false);
      onError(
        "Подложка карты не загрузилась. Проверьте интернет и повторите. Список проектов остаётся доступен.",
      );
    };
    instance.on("idle", loaded);
    instance.on("error", failed);
    const lostContext = () => {
      onLoading(false);
      onError(
        "Отрисовка карты прервалась. Повторите загрузку карты. Список проектов доступен ниже.",
      );
    };
    instance.getCanvas().addEventListener("webglcontextlost", lostContext);
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(container.current);
    setMap(instance);
    return () => {
      resize.disconnect();
      popupRef.current?.remove();
      popupRef.current = null;
      instance.getCanvas().removeEventListener("webglcontextlost", lostContext);
      instance.remove();
    };
  }, [onLoading, onError]);

  useEffect(() => {
    if (!map) return;
    const markers = projects.map((project) => {
      const { candidate, location } = project;
      const priority = projectPriority(candidate.score);
      const label = priorities.find((item) => item.id === priority)!.label;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "project-map-marker";
      button.setAttribute(
        "aria-label",
        `${candidate.company}, ${candidate.city}, приоритет ${label.toLowerCase()}, ${candidate.score} из 100`,
      );
      button.title = `${candidate.company} · ${label} приоритет · ${candidate.score}/100`;
      const dot = document.createElement("span");
      dot.className = `map-point priority-${priority}`;
      dot.setAttribute("aria-hidden", "true");
      button.append(dot);
      const coordinates: [number, number] = [
        location.longitude,
        location.latitude,
      ];
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        popupRef.current?.remove();
        const host = document.createElement("div");
        const popup = new Popup({
          offset: 16,
          maxWidth: "280px",
          focusAfterOpen: false,
          closeButton: true,
        })
          .setLngLat(coordinates)
          .setDOMContent(host)
          .addTo(map);
        popupRef.current = popup;
        popup
          .getElement()
          .querySelector(".maplibregl-popup-close-button")
          ?.setAttribute("aria-label", "Закрыть окно проекта");
        popup.on("close", () => {
          if (popupRef.current === popup) {
            popupRef.current = null;
            setSelection(null);
          }
        });
        setSelection({ project, host });
      });
      return new Marker({ element: button, anchor: "center" })
        .setLngLat(coordinates)
        .addTo(map);
    });
    if (projects.length) {
      const bounds = new LngLatBounds();
      projects.forEach(({ location }) =>
        bounds.extend([location.longitude, location.latitude]),
      );
      map.fitBounds(bounds, { padding: 45, maxZoom: 11, duration: 0 });
    } else map.jumpTo({ center: DEFAULT_CENTER, zoom: 8 });
    return () => {
      popupRef.current?.remove();
      markers.forEach((marker) => marker.remove());
    };
  }, [map, projects, reset]);

  useEffect(() => {
    if (!selection) return;
    selection.host
      .querySelector<HTMLAnchorElement>(".map-popup-link")
      ?.focus({ preventScroll: true });
  }, [selection]);

  return (
    <>
      <div ref={container} className="project-map" />
      {map && (
        <div className="map-zoom" role="group" aria-label="Масштаб карты">
          <button
            type="button"
            onClick={() => map.zoomIn({ duration: 0 })}
            aria-label="Приблизить карту"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => map.zoomOut({ duration: 0 })}
            aria-label="Отдалить карту"
          >
            −
          </button>
        </div>
      )}
      {selection &&
        createPortal(<ProjectPopup {...selection.project} />, selection.host)}
    </>
  );
}

function ProjectPopup({ candidate, location }: MapProject) {
  const priority = projectPriority(candidate.score);
  const label = priorities.find((item) => item.id === priority)!.label;
  return (
    <div className="map-popup">
      <span className="eyebrow">{candidate.id}</span>
      <h2>{candidate.company}</h2>
      <p>
        {candidate.city} · {candidate.sector}
      </p>
      <div className="map-popup-priority">
        <span
          className={`priority-dot priority-${priority}`}
          aria-hidden="true"
        />
        {label} приоритет · <strong>{candidate.score}/100</strong>
      </div>
      <StatusBadge status={candidate.status} />
      <p className="map-popup-note">
        {location.precision === "municipality"
          ? "Примерная точка в муниципалитете"
          : "Расположение проекта"}
      </p>
      <Link
        className="map-popup-link"
        to={`/queue?case=${encodeURIComponent(candidate.id)}`}
      >
        Открыть карточку <ArrowUpRight size={15} aria-hidden="true" />
      </Link>
    </div>
  );
}
