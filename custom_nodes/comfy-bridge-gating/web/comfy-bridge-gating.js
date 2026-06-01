// comfy-bridge gating: two-tier filter for upstream api_nodes.
//
// Tier 1 (vendor): if a node's vendor (parsed from python_module
//                  `comfy_api_nodes.nodes_<vendor>`) is NOT in allowed_vendors,
//                  the class is removed from LiteGraph's node registry entirely
//                  -> it disappears from the right-click "Add Node" menu.
// Tier 2 (class):  if vendor is allowed but the class isn't on allowed_node_classes,
//                  the class is greyed and tagged [未适配] (current + future instances).
//
// Why setup-time (not beforeRegisterNodeDef): ComfyUI calls beforeRegisterNodeDef
// for every node BEFORE setup runs. The allowlist fetch is async, so we'd race.
// We stash nodeData in beforeRegisterNodeDef and apply filters in setup once
// fetch resolves. On bridge unreachable -> fail-open (no filter).
import { app } from "../../scripts/app.js";

const BRIDGE_GATING_URL = "http://127.0.0.1:8190/comfy-bridge/gating";

const API_NODES = new Map();  // typeName -> { nodeType, nodeData }

function parseVendor(pythonModule) {
  // "comfy_api_nodes.nodes_openai" -> "openai"
  // anything else -> null (treat as unknown -> hide)
  if (typeof pythonModule !== "string") return null;
  const m = pythonModule.match(/^comfy_api_nodes\.nodes_([a-zA-Z0-9_]+)$/);
  return m ? m[1].toLowerCase() : null;
}

async function loadGating() {
  try {
    const resp = await fetch(BRIDGE_GATING_URL, { method: "GET", credentials: "omit" });
    if (resp.ok) return await resp.json();
    console.warn("[comfy-bridge] gating HTTP", resp.status, "- failing open");
  } catch (e) {
    console.warn("[comfy-bridge] gating fetch failed - failing open:", e);
  }
  return { gating_enabled: false, allowed_vendors: [], allowed_node_classes: [], hidden_node_classes: [] };
}

function hideClass(cls) {
  // Remove from LiteGraph registry -> won't appear in "Add Node" menu.
  // Existing canvas instances still render as "missing node" placeholder,
  // which is fine since we're hiding vendors the user doesn't support anyway.
  if (window.LiteGraph?.registered_node_types?.[cls]) {
    delete window.LiteGraph.registered_node_types[cls];
  }
}

function applyGreyOverride(nodeType) {
  const origOnAdded = nodeType.prototype.onAdded;
  nodeType.prototype.onAdded = function () {
    if (origOnAdded) origOnAdded.apply(this, arguments);
    this.color = "#3a3a3a";
    this.bgcolor = "#2a2a2a";
    if (this.title && !this.title.includes("未适配")) {
      this.title = `[未适配] ${this.title}`;
    }
    (this.widgets || []).forEach((w) => { w.disabled = true; });
  };
  const origDraw = nodeType.prototype.onDrawForeground;
  nodeType.prototype.onDrawForeground = function (ctx) {
    if (origDraw) origDraw.apply(this, arguments);
    ctx.save();
    ctx.fillStyle = "rgba(255,80,80,0.85)";
    ctx.font = "11px sans-serif";
    ctx.fillText("comfy-bridge: 未适配", 8, this.size[1] - 8);
    ctx.restore();
  };
}

function updateExistingInstances(graph, cls) {
  if (!graph || !graph._nodes) return;
  for (const node of graph._nodes) {
    if (node.type !== cls) continue;
    node.color = "#3a3a3a";
    node.bgcolor = "#2a2a2a";
    if (node.title && !node.title.includes("未适配")) {
      node.title = `[未适配] ${node.title}`;
    }
    (node.widgets || []).forEach((w) => { w.disabled = true; });
  }
}

app.registerExtension({
  name: "comfy-bridge.gating",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData && nodeData.api_node) {
      API_NODES.set(nodeData.name, { nodeType, nodeData });
    }
  },
  async setup() {
    const gating = await loadGating();
    if (!gating.gating_enabled) {
      console.log("[comfy-bridge] gating disabled, no filtering");
      return;
    }
    const allowedVendors = new Set(gating.allowed_vendors || []);
    const allowedClasses = new Set(gating.allowed_node_classes || []);
    const hiddenClasses = new Set(gating.hidden_node_classes || []);
    // backend capability authority (spec §4.4 v7): which node classes the
    // currently-loaded backends actually support, + vendor reverse-lookup map.
    const loadedNodeClasses = new Set(gating.loaded_node_classes || []);
    const vendorMeta = gating.vendor_meta || {};
    let hidden = 0, greyed = 0, kept = 0;
    for (const [cls, { nodeType, nodeData }] of API_NODES.entries()) {
      // Per-class hard hide (denylist) wins over vendor allow: removes the class from
      // the menu entirely, for an allowed vendor whose gateway can't serve this node.
      if (hiddenClasses.has(cls)) {
        hideClass(cls);
        hidden++;
        continue;
      }
      const vendor = parseVendor(nodeData.python_module);
      if (!vendor || !allowedVendors.has(vendor)) {
        hideClass(cls);
        hidden++;
        continue;
      }

      // ── 新增（spec §4.4 v7 + codex v6 P1-3）──
      // loaded_node_classes 是 backend capability authority. 若 vendor 已声明
      // 但当前 backend 不支持此 class (如 Linux 切 fal-ai 时 Seedance 1.x 4 节点)，
      // hide. 这是叠加在 vendor allowlist 之上的 capability 硬约束.
      const inVendorMeta = Object.values(vendorMeta).some(
        (meta) => meta.python_module_segment === vendor
      );
      if (inVendorMeta && !loadedNodeClasses.has(cls)) {
        hideClass(cls);
        hidden++;
        continue;
      }
      // ── 新增结束 ──

      if (!allowedClasses.has(cls)) {
        applyGreyOverride(nodeType);
        updateExistingInstances(app.graph, cls);
        greyed++;
        continue;
      }
      kept++;
    }
    console.log(
      `[comfy-bridge] gating applied: ${kept} allowed, ${greyed} greyed, ${hidden} hidden`,
      `(total api_nodes: ${API_NODES.size}, allowed vendors: [${[...allowedVendors].join(",")}])`,
    );
    if (app.graph) app.graph.setDirtyCanvas(true, true);
  },
});
