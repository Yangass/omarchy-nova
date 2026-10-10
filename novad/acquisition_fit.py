"""Read-only exact-revision local metadata for absent acquisition artifacts."""
import json
from pathlib import Path
from model_fit import evaluate
import catalog_repins
import requested_models

PATH = Path(__file__).resolve().parent.parent / 'catalog/acquisition_fit.json'
FIT_SOURCES = {'local_ollama_api_show_and_exact_manifest', 'local_show_or_registry_gguf_probe'}


def saved_fit(record, snapshot):
    try:
        # User-requested models (QWEN-08 store) keep their probe fit beside their catalog record.
        if isinstance(record.get('catalog_id'), str) and record['catalog_id'].startswith('requested-'):
            path = requested_models.default_store() / 'acquisition_fit.json'
        elif record.get('repinned'):  # Ollama re-published the tag; the local re-pin keeps its probe fit.
            path = catalog_repins.fit_path()
        else:
            path = PATH
        with path.open('rb') as file: raw = file.read(65537)
        if len(raw) > 65536: return None
        data = json.loads(raw)
        # Records carry their own fit_metadata_source: local `ollama show` or a registry GGUF-header probe.
        if data['version'] != 1 or data['source'] not in FIT_SOURCES: return None
        source = record['source']
        matches = [r for r in data['records'] if r['catalog_id'] == record['catalog_id'] and
                   r['provider'] == source['provider'] and r['artifact_id'] == source['upstream_id'] and
                   r['revision'] == source['revision_digest']]
        if len(matches) != 1: return None
        row = matches[0]; metadata = row['metadata']; properties = record['properties']
        if (metadata['size_bytes'], metadata['architecture'], metadata['context_length']) != (
                properties['weight_size_bytes'], properties['architecture'], properties['native_context']): return None
        for value in [metadata['size_bytes'], metadata['context_length'], *metadata['attention'].values()]:
            if value is not None and (type(value) is not int or value <= 0): return None
        if any(type(v) is not bool and v is not None for v in row['capabilities'].values()): return None
        model = {'installed': False, 'available': snapshot['provider_available'],
                 'metadata': metadata, 'capabilities': row['capabilities']}
        return evaluate(model, snapshot['budget'], known_artifact=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError): return None
