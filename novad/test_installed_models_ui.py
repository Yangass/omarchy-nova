"""Static contract checks for the installed-models section (CODE-01A.1)."""
import os
from pathlib import Path
import unittest


PLUGIN_DIR = Path(os.environ.get(
    'NOVA_PLUGIN_DIR',
    Path(__file__).resolve().parents[1]))
PANEL = (PLUGIN_DIR / 'Panel.qml').read_text()


class InstalledModelsUiContractTests(unittest.TestCase):
    def test_installed_inventory_properties_exist(self):
        self.assertIn('property bool installedModelsLoading', PANEL)
        self.assertIn('property var modelsRequestTimeoutCallback', PANEL)
        self.assertIn('property int modelsRequestSerial', PANEL)

    def test_installed_section_is_distinct_and_bound_to_provider_models(self):
        self.assertIn('objectName: "installedModelsSection"', PANEL)
        self.assertIn('objectName: "installedModelsRepeater"', PANEL)
        self.assertIn('Repeater { model: root.discoveredModels', PANEL)
        installed = PANEL[PANEL.index('id: installedModelsSection'):
                          PANEL.index('id: catalogModelRepeater')]
        self.assertIn('modelData.id', installed)

    def test_refresh_control_is_explicit_focusable_and_uses_existing_endpoint(self):
        self.assertIn('objectName: "refreshInstalledModelsButton"', PANEL)
        self.assertIn('Accessible.name: "Refresh installed models"', PANEL)
        button = PANEL[PANEL.index('objectName: "refreshInstalledModelsButton"'):
                       PANEL.index('objectName: "installedModelsRepeater"')]
        self.assertIn('focusable: true', button)
        self.assertIn('enabled: !root.installedModelsLoading', button)
        self.assertIn('onClicked: root.refreshModels()', button)

    def test_loading_and_empty_states_are_truthful_not_a_loading_placeholder(self):
        self.assertIn('REFRESHING INSTALLED INVENTORY…', PANEL)
        self.assertIn('NO MODELS INSTALLED YET', PANEL)
        self.assertNotIn('Loading…', PANEL[PANEL.index('id: modelSelector'):])

    def test_refresh_guards_against_overlap_and_stale_replies(self):
        start = PANEL.index('function refreshModels')
        end = PANEL.index('function refreshFit')
        region = PANEL[start:end]
        self.assertIn('if (installedModelsLoading) return', region)
        self.assertIn('var serial = ++modelsRequestSerial', region)
        self.assertIn('if (finished) return', region)
        self.assertIn('if (serial !== modelsRequestSerial) return', region)
        self.assertIn('modelsRequestDeadline.stop()', region)
        self.assertIn('installedModelsLoading = false', region)

    def test_accepts_only_http_200_with_models_array(self):
        start = PANEL.index('function applyInstalledModelsResult')
        end = PANEL.index('function refreshFit')
        region = PANEL[start:end]
        self.assertIn('status === 200 ? JSON.parse(body) : null', region)
        self.assertIn('Array.isArray(payload.models)', region)
        self.assertIn('discoveredModels = []', region)
        self.assertIn('modelsUnavailable = true', region)

    def test_installed_section_keeps_exact_inventory_identity_separate_from_catalog(self):
        installed = PANEL[PANEL.index('id: installedModelsSection'):
                          PANEL.index('id: catalogModelRepeater')]
        # The installed row is the primary row for exact installed identities.
        self.assertIn('root.loadInstalledModel(modelData)', installed)
        self.assertIn('root.installedModelReady(modelData)', installed)
        self.assertIn('root.installedModelSelected(modelData)', installed)
        self.assertIn('Accessible.name: text + " " + modelData.id', installed)
        # The installed row has an explicit details control for matched catalog data.
        self.assertIn('installedModelDetailsButton', installed)
        self.assertIn('root.installedModelDetailsOpen(modelData)', installed)
        self.assertIn('root.toggleInstalledModelDetails(modelData)', installed)
        # Catalog-specific helpers are not directly in the installed section.
        self.assertNotIn('catalogRuntimeId', installed)
        self.assertNotIn('catalogIsInstalled', installed)
        # The installed row does NOT have an ACTIVATE button (catalog row's role).
        self.assertNotIn('activateModelButton', installed)
        # The catalog repeater conditionally hides exact installed identities.
        catalog_region = PANEL[PANEL.index('id: catalogModelRepeater'):
                               PANEL.index('id: fixedBottom')]
        self.assertIn('root.catalogRowVisible(modelData)', catalog_region)

    def test_catalog_and_existing_refresh_behavior_are_preserved(self):
        self.assertIn('refreshModels(); refreshCatalog()', PANEL)
        selector = PANEL[PANEL.index('id: modelSelector'):]
        self.assertIn('root.catalogRuntimeId(modelData)', selector)
        self.assertIn('Repeater { model: root.orderedCatalogModels', selector)
        self.assertGreaterEqual(selector.count('wrapMode: Text.Wrap'), 7)


if __name__ == '__main__':
    unittest.main()
