import unittest
from webmanager.core import map as map_module
from webmanager.core.paths import WEB_DIR


class TestMapTilesAndTelemetry(unittest.TestCase):
    def test_coordinate_calibration_for_24km_tile_grid(self):
        # Origin (0, 0) maps to center (3072.0, 3072.0)
        self.assertEqual(map_module.game_to_map_pixel(0.0, 0.0), (3072.0, 3072.0))
        # World edges (-12000, -12000) and (+12000, +12000)
        self.assertEqual(map_module.game_to_map_pixel(-12000.0, -12000.0), (0.0, 6144.0))
        self.assertEqual(map_module.game_to_map_pixel(12000.0, 12000.0), (6144.0, 0.0))
        # Midgard 10,500m radius maps to 2688.0px
        self.assertEqual(map_module.MAP_DISK_RADIUS_PX, 2688.0)

    def test_tile_pyramid_and_telemetry_assets_exist(self):
        tiles_root = WEB_DIR / "static" / "img" / "tiles"
        self.assertTrue((tiles_root / "0" / "0" / "0.webp").exists())
        self.assertTrue((tiles_root / "4" / "8" / "8.webp").exists())
        self.assertTrue((tiles_root / "telemetry_1024.bin").exists())
        self.assertEqual((tiles_root / "telemetry_1024.bin").stat().st_size, 1024 * 1024 * 4)

    def test_sample_terrain_at_spawn_altar(self):
        # Spawn altar is around X=90.8, Z=36.0 in Meadows above sea level (30m)
        sample = map_module.sample_terrain_at(90.8, 36.0)
        self.assertEqual(sample["biome"], "Meadows")
        self.assertEqual(sample["biome_code"], 1)
        self.assertGreater(sample["height"], 30.0)
        self.assertGreater(sample["altitude_asl"], 0.0)

    def test_sample_terrain_outside_disk_is_world_edge(self):
        sample = map_module.sample_terrain_at(11500.0, 11500.0)
        self.assertEqual(sample["biome"], "World Edge")
        self.assertEqual(sample["biome_code"], 0)

    def test_world_map_data_includes_tile_and_telemetry_metadata(self):
        data = map_module.get_world_map_data(force_refresh=True)
        self.assertTrue(data["tiles_url"].startswith("/static/img/tiles/{z}/{x}/{y}.webp"))
        self.assertEqual(data["tile_size"], 1024)
        self.assertEqual(data["max_native_zoom"], 4)
        self.assertEqual(data["telemetry_url"], "/static/img/tiles/telemetry_1024.bin")
        self.assertEqual(data["bounds"]["world_width"], 24000.0)
        if data["portals"]:
            first = data["portals"][0]
            self.assertIn("biome", first)
            self.assertIn("biome_icon", first)

    def test_map_js_preserves_route_selection_on_view_both_ends(self):
        map_js = (WEB_DIR / "static" / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn("function syncSelectionAndHoverHighlights()", map_js)
        self.assertIn("window.zoomToShowPortalRoute = function", map_js)
        self.assertNotIn("marker.on('popupclose'", map_js)
        self.assertIn("event.stopPropagation(); zoomToShowPortalRoute", map_js)
        self.assertIn("drawer-active", map_js)
        self.assertIn("parseSearchFilters", map_js)
        self.assertIn("updateDrawerVisibleHighlights", map_js)
        self.assertIn("function updateDrawerSelectedPanel(portalId)", map_js)
        self.assertIn("window.updateDrawerSelectedPanel = updateDrawerSelectedPanel", map_js)
        # Portals no longer use floating map popups
        render_portals_code = map_js.split("function renderPortals(")[1].split("function updatePortalLayoutForZoom")[0]
        self.assertNotIn("marker.bindPopup", render_portals_code)
        # Fly to portal uses cinematic 2-phase zoom animation
        self.assertIn("window.flyToPortal = function", map_js)
        self.assertIn("distMeters > 80", map_js)
        self.assertIn("flyToBounds", map_js)

    def test_directory_selected_panel_in_templates_and_css(self):
        map_tab = (WEB_DIR / "templates" / "partials" / "map_tab.html").read_text(encoding="utf-8")
        self.assertIn('id="map-drawer-selected-panel"', map_tab)
        self.assertIn('class="drawer-selected-panel"', map_tab)

        style_css = (WEB_DIR / "static" / "css" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".drawer-selected-panel", style_css)
        self.assertIn(".selected-peer-card", style_css)
        self.assertIn(".drawer-portal-item.is-selected", style_css)
        self.assertIn(".map-directory-btn.is-open", style_css)


if __name__ == "__main__":
    unittest.main()
