// Single entry point for the bundled Shotstack Studio editor. A real bundler (esbuild) resolves every
// dependency - pixi.js included - to exactly one copy, which CDN import maps could not guarantee.
// app.js reaches these through the global `HookTrackStudioBundle` instead of a dynamic CDN import().
export { Edit, Canvas, Controls, Timeline, UIController, VideoExporter, ExportError, WebGLUnsupportedError } from "@shotstack/shotstack-studio";
