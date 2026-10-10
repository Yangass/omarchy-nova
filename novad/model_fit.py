"""Deterministic, conditional operating fit, never overall model quality/ranking."""
from dataclasses import asdict, dataclass
from hardware import number

GIB = 1024 ** 3

@dataclass(frozen=True)
class HeadroomPolicy:
    gpu_reserve_min_bytes: int = 2 * GIB
    gpu_reserve_fraction: float = .15
    ram_reserve_min_bytes: int = 4 * GIB
    ram_reserve_fraction: float = .25
    runtime_min_bytes: int = GIB
    runtime_weights_fraction: float = .10
    kv_safety_factor: float = 1.25
    cache_element_bytes: int = 2
    target_context: int = 32768
    minimum_context: int = 4096
    context_step: int = 1024
    excellent_margin_fraction: float = .15
    good_margin_fraction: float = .05

POLICY = HeadroomPolicy()
PRINCIPLE = 'Prefer the best sustainable model with strong usable context and adequate system headroom over the largest model that barely loads. Fit is not model quality.'


def positive(value):
    n = number(value)
    return n if n is not None and n > 0 else None


# Graphics without dedicated memory and under this much of their own (Intel/AMD
# integrated, virtual GPUs) cannot hold a model; they share system RAM.
INTEGRATED_VRAM_LIMIT_BYTES = 2 * GIB


def _integrated(device):
    return (device.get('memory_kind') != 'dedicated' and
            (positive(device.get('vram_total_bytes')) or 0) < INTEGRATED_VRAM_LIMIT_BYTES)


def _gib(bytes_value):
    """Format whole bytes as a GiB label: at most one decimal, drop a trailing .0."""
    value = round(bytes_value / GIB, 1)
    return str(int(value)) if value == int(value) else str(value)


def _explanation(mode, capacity_bytes, reserve_bytes, operating_bytes, graphics=None):
    """Plain-language reason the model list is short, from the budget numbers."""
    if mode == 'gpu_candidate':
        return (f'NOVA keeps {_gib(reserve_bytes)} GiB of your {_gib(capacity_bytes)} GiB '
                f'graphics memory free for the system, so models can use up to {_gib(operating_bytes)} GiB.')
    if mode == 'cpu' and capacity_bytes is not None:
        prefix = "This computer's graphics share its main memory" if graphics == 'integrated_or_virtual' else 'No graphics card was found'
        return (f"{prefix}, so models run in RAM. NOVA keeps {_gib(reserve_bytes)} GiB for your desktop and apps, "
                f"which leaves about {_gib(operating_bytes)} GiB for a model. "
                f"Larger models are listed but can't be installed here.")
    return 'NOVA could not measure your memory reliably, so it cannot say which models fit yet.'


def operating_budget(profile, policy=POLICY):
    capacity = profile.get('capacity', {})
    devices = capacity.get('gpus', [])
    candidates = [d for d in devices if d.get('memory_kind') == 'dedicated' and positive(d.get('vram_total_bytes'))]
    if len(candidates) == 1:
        device = candidates[0]
        total = positive(device['vram_total_bytes'])
        reserve = min(total, max(policy.gpu_reserve_min_bytes, int(total*policy.gpu_reserve_fraction)))
        return {'mode': 'gpu_candidate', 'device': device['id'], 'capacity_bytes': total,
                'reserve_bytes': reserve, 'operating_budget_bytes': total-reserve,
                'assumption': 'Conditional assessment of the sole known dedicated GPU, not a detected inference-device selection. Other devices are not combined; Ollama full offload is unverified.',
                'explanation': _explanation('gpu_candidate', total, reserve, total-reserve)}
    # A dedicated or sizeable GPU of uncertain memory (or several) stays unknown;
    # only integrated/virtual graphics fall back to the system-memory estimate.
    if devices and not all(_integrated(d) for d in devices):
        return {'mode': 'unknown', 'device': None, 'capacity_bytes': None, 'reserve_bytes': None,
                'operating_budget_bytes': None, 'assumption': 'GPU capacity, dedicated memory, or inference device is uncertain. VRAM is not summed.',
                'explanation': _explanation('unknown', None, None, None)}
    total = positive(capacity.get('memory', {}).get('total_bytes'))
    reserve = min(total, max(policy.ram_reserve_min_bytes, int(total*policy.ram_reserve_fraction))) if total else None
    assumption = 'CPU/RAM memory fit only; speed is not predicted.'
    if devices:
        assumption = ('Integrated or virtual graphics without dedicated memory share system RAM; '
                      'fit uses system memory. Speed is not predicted.')
    graphics = 'integrated_or_virtual' if devices else 'none_detected'
    return {'mode': 'cpu', 'device': None, 'capacity_bytes': total, 'reserve_bytes': reserve,
            'operating_budget_bytes': total-reserve if total else None, 'assumption': assumption,
            'graphics': graphics,
            'explanation': _explanation('cpu', total, reserve, total-reserve if total else None, graphics)}


# Every layer caches the full context (conventional GQA attention).
FULL_ATTENTION = ('qwen3', 'qwen3moe', 'llama', 'phi3')
# Only `full_attention_layers` cache the full context; the rest are recurrent
# (qwen35) or sliding-window (gemma4) with a context-independent cache.
PATTERNED = ('qwen35', 'qwen35moe', 'gemma4')
# Sliding-window layers without a per-layer pattern in metadata: count every
# layer as full attention, a deliberate overestimate (never claims a false fit).
UPPER_BOUND = ('gptoss', 'gpt-oss')  # Ollama's GGUF re-publish of 2026-10 spells it gpt-oss


def kv_profile(metadata, policy=POLICY):
    """(bytes per context token, fixed cache bytes, method) or None to abstain."""
    arch = metadata.get('architecture')
    kv = metadata.get('attention', {})
    values = [positive(kv.get(key)) for key in ('layers', 'kv_heads', 'key_length', 'value_length')]
    if not all(values): return None
    layers, heads, key, value = values
    if layers > 1024 or heads > 1024 or key > 8192 or value > 8192: return None
    scale = policy.cache_element_bytes * policy.kv_safety_factor
    if arch in FULL_ATTENTION:
        return int(layers * heads * (key+value) * scale), 0, 'metadata_full_attention_fp16_with_25pct_margin'
    if arch in UPPER_BOUND:
        return int(layers * heads * (key+value) * scale), 0, 'upper_bound_all_layers_full_attention_fp16_with_25pct_margin'
    if arch in PATTERNED:
        full = positive(kv.get('full_attention_layers'))
        if not full or full > layers: return None
        window = kv.get('window_cache_elements', 0)
        if type(window) is not int or window < 0: return None
        return (int(full * heads * (key+value) * scale), int(window * scale),
                'metadata_layer_pattern_fp16_with_25pct_margin')
    return None  # unknown architectures abstain


def kv_bytes_per_token(metadata, policy=POLICY):
    profile = kv_profile(metadata, policy)
    return profile[0] if profile else None


def evaluate(model, budget, policy=POLICY, *, known_artifact=False):
    metadata = model.get('metadata') or {}
    weights = positive(metadata.get('size_bytes'))
    native = positive(metadata.get('context_length'))
    total = budget['operating_budget_bytes']
    result = {'rating': 'UNKNOWN', 'confidence': 'low', 'weights_bytes': weights,
              'weights_source': 'installed artifact size; proxy for resident weights',
              'native_context': native, 'recommended_context': None, 'context_fit_confidence': 'unknown',
              'estimated_runtime_bytes': None, 'estimated_headroom_bytes': None,
              'weight_headroom_bytes': total-weights if total is not None and weights else None,
              'gpu_offload': 'unknown', 'notes': []}
    if model.get('capabilities', {}).get('chat') is False:
        result.update(rating='UNSUPPORTED'); result['notes'].append('Backend metadata reports no chat capability.'); return result
    if (not model.get('installed') and not known_artifact) or not model.get('available') or weights is None or total is None:
        result['notes'].append('Installed/provider state, weights, or operating capacity is unknown.'); return result
    overhead = max(policy.runtime_min_bytes, int(weights*policy.runtime_weights_fraction))
    profile = kv_profile(metadata, policy)
    if profile and profile[1]:
        # Context-independent sliding-window cache is part of the fixed allowance.
        overhead += profile[1]
        result['fixed_cache_bytes'] = profile[1]
    result['runtime_reserve_bytes'] = overhead
    result['notes'].append(budget['assumption'])
    result['notes'].append('Estimates assume one text sequence, FP16 KV cache, and full weights in the selected memory pool. Context is advisory, not applied to chat.')
    bpt = profile[0] if profile else None
    result['context_method'] = profile[2] if profile else 'unknown_architecture_or_metadata'
    result['kv_bytes_per_token_estimate'] = bpt
    if weights + overhead > total:
        result['rating'] = 'POOR'
        result['notes'].append('Weights plus runtime allowance exceed the operating budget before context. Partial/CPU offload or reduced headroom would be required.')
        result['gpu_offload'] = 'partial_or_cpu_expected' if budget['mode'] == 'gpu_candidate' else 'none'
        return result
    if not bpt or not native:
        result['notes'].append('Context memory cannot be bounded responsibly; overall fit remains UNKNOWN despite the weight margin.')
        return result
    # Keep a further working margin rather than consuming all residual memory for KV.
    max_tokens = int(max(0, total-weights-overhead-int(total*policy.good_margin_fraction)) // bpt)
    recommended = min(native, policy.target_context, max_tokens)
    recommended = recommended // policy.context_step * policy.context_step
    if recommended < min(native, policy.minimum_context):
        result['rating'] = 'POOR'
        result['notes'].append('Insufficient budget for the minimum practical context under this policy.')
        return result
    runtime = weights + overhead + recommended*bpt
    margin = total-runtime
    result.update(recommended_context=recommended, context_fit_confidence='medium', estimated_runtime_bytes=runtime,
                  estimated_headroom_bytes=margin, confidence='medium',
                  gpu_offload='full_estimated' if budget['mode'] == 'gpu_candidate' else 'none')
    if recommended < min(native, policy.target_context): result['rating'] = 'TIGHT'
    elif margin >= total*policy.excellent_margin_fraction: result['rating'] = 'EXCELLENT'
    elif margin >= total*policy.good_margin_fraction: result['rating'] = 'GOOD'
    else: result['rating'] = 'TIGHT'
    result['notes'].append('Headroom is unused operating budget after weights, runtime allowance and estimated KV; system reserve is additional.')
    return result


def fit_response(profile, models, observed=None, runtime_available=False, policy=POLICY):
    budget = operating_budget(profile, policy)
    rows = []
    for model in models:
        item = dict(model)
        item['fit'] = evaluate(model, budget, policy)
        resident = next((r for r in (observed or []) if r.get('name') == model['id'] or r.get('model') == model['id']), None)
        item['observed_runtime'] = {'source': 'ollama/api/ps', 'available': runtime_available,
                                    'resident': resident is not None if runtime_available else None,
                                    'runtime_bytes': number(resident.get('size')) if resident else None,
                                    'vram_bytes': number(resident.get('size_vram')) if resident else None,
                                    'context_length': positive(resident.get('context_length')) if resident else None}
        rows.append(item)
    return {'schema_version': 1, 'principle': PRINCIPLE, 'provider_available': profile['local_ai']['available'],
            'hardware': profile, 'budget': budget, 'policy': asdict(policy), 'models': rows}
