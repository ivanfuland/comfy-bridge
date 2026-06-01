// comfy-bridge gating: vendor allowlist + class denylist for upstream api_nodes.
//
// A node is HIDDEN (removed from LiteGraph's registry -> gone from the "Add Node"
// menu) when ANY of:
//   - it is in hidden_node_classes (denylist; wins over everything), or
//   - its vendor (parsed from python_module `comfy_api_nodes.nodes_<vendor>`) is
//     NOT in allowed_vendors, or
//   - its vendor has a registered backend but the currently-loaded backend does NOT
//     list the class as supported (loaded_node_classes capability check; e.g. Seedance
//     1.x when the byteplus vendor is routed to fal-ai).
// Everything else is shown as-is. There is NO "greyed / 未适配" middle state: a node is
// either shown or hidden. To hide a node manually, add it to BRIDGE_HIDDEN_NODE_CLASSES.
//
// Why setup-time (not beforeRegisterNodeDef): ComfyUI calls beforeRegisterNodeDef
// for every node BEFORE setup runs. The gating fetch is async, so we'd race. We stash
// nodeData in beforeRegisterNodeDef and apply filters in setup once fetch resolves.
// On bridge unreachable -> fail-open (no filter).
import { app } from "../../scripts/app.js";

const BRIDGE_GATING_URL = "http://127.0.0.1:8190/comfy-bridge/gating";

const API_NODES = new Map();  // typeName -> { nodeData }

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
  return { gating_enabled: false, allowed_vendors: [], hidden_node_classes: [] };
}

function hideClass(cls) {
  // Remove from LiteGraph registry -> won't appear in "Add Node" menu.
  // Existing canvas instances still render as "missing node" placeholder,
  // which is fine since we're hiding vendors/nodes the user doesn't support anyway.
  if (window.LiteGraph?.registered_node_types?.[cls]) {
    delete window.LiteGraph.registered_node_types[cls];
  }
}

app.registerExtension({
  name: "comfy-bridge.gating",
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData && nodeData.api_node) {
      API_NODES.set(nodeData.name, { nodeData });
    }
  },
  async setup() {
    const gating = await loadGating();
    if (!gating.gating_enabled) {
      console.log("[comfy-bridge] gating disabled, no filtering");
      return;
    }
    const allowedVendors = new Set(gating.allowed_vendors || []);
    const hiddenClasses = new Set(gating.hidden_node_classes || []);
    // backend capability authority: which classes the currently-loaded backends
    // support, + the vendor reverse-lookup map.
    const loadedNodeClasses = new Set(gating.loaded_node_classes || []);
    const vendorMeta = gating.vendor_meta || {};
    let hidden = 0, shown = 0;
    for (const [cls, { nodeData }] of API_NODES.entries()) {
      // denylist wins over everything
      if (hiddenClasses.has(cls)) { hideClass(cls); hidden++; continue; }
      const vendor = parseVendor(nodeData.python_module);
      if (!vendor || !allowedVendors.has(vendor)) { hideClass(cls); hidden++; continue; }
      // capability: vendor has a backend but this class isn't supported by the loaded
      // backend -> hide outright (no grey state).
      const inVendorMeta = Object.values(vendorMeta).some(
        (meta) => meta.python_module_segment === vendor
      );
      if (inVendorMeta && !loadedNodeClasses.has(cls)) { hideClass(cls); hidden++; continue; }
      shown++;
    }
    console.log(
      `[comfy-bridge] gating applied: ${shown} shown, ${hidden} hidden`,
      `(total api_nodes: ${API_NODES.size}, allowed vendors: [${[...allowedVendors].join(",")}])`,
    );
    if (app.graph) app.graph.setDirtyCanvas(true, true);
  },
});
