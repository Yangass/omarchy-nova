import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path
import hardware
import model_fit as fit
import server
from providers import OllamaProvider, ProviderUnavailable

G=fit.GIB

def profile(gpu=True):
    return {'capacity': {'memory': {'total_bytes': 32*G}, 'gpus': [{'id':'gpu0','memory_kind':'dedicated','vram_total_bytes':32*G}] if gpu else []},
            'dynamic': {'memory': {'available_bytes': 20*G}}, 'local_ai': {'available': True}}

def model(size=8*G):
    return {'id':'arbitrary-label','provider':'ollama','installed':True,'available':True,
            'capabilities': {'chat':True}, 'metadata': {'size_bytes':size,'architecture':'qwen3', 'context_length':131072,
            'attention': {'layers':32,'kv_heads':8,'key_length':128,'value_length':128}}}

class FitTests(unittest.TestCase):
    def evaluate(self,m,p=None): return fit.evaluate(m,fit.operating_budget(p or profile()))
    def test_context_first_and_headroom_order(self):
        small,big=self.evaluate(model()),self.evaluate(model(22*G))
        self.assertEqual(small['rating'],'EXCELLENT')
        self.assertGreater(small['estimated_headroom_bytes'],big['estimated_headroom_bytes'])
        self.assertLess(big['recommended_context'],small['recommended_context'])
        self.assertEqual(big['rating'],'TIGHT')
    def test_name_does_not_determine_fit(self):
        m=model(); a=self.evaluate(m); m['id']='fake:999999b'
        self.assertEqual(a,self.evaluate(m))
    def test_hybrid_abstains(self):
        m=model(); m['metadata']['architecture']='qwen35moe'
        r=self.evaluate(m)
        self.assertEqual(r['rating'],'UNKNOWN'); self.assertIsNone(r['recommended_context'])
        self.assertIsNone(r['estimated_headroom_bytes'])
        self.assertGreater(r['weight_headroom_bytes'],0)
    def test_standard_full_attention_architectures_use_exact_formula(self):
        for arch in ('llama', 'phi3'):
            m=model(); m['metadata']['architecture']=arch
            r=self.evaluate(m)
            self.assertEqual(r['kv_bytes_per_token_estimate'], int(32*8*256*2*1.25))
            self.assertEqual(r['context_method'], 'metadata_full_attention_fp16_with_25pct_margin')
            self.assertNotEqual(r['rating'], 'UNKNOWN')
    def test_hybrid_with_layer_pattern_counts_only_full_attention_layers(self):
        m=model(); m['metadata']['architecture']='qwen35'
        m['metadata']['attention'].update(layers=64, full_attention_layers=16)
        r=self.evaluate(m)
        self.assertEqual(r['kv_bytes_per_token_estimate'], int(16*8*256*2*1.25))
        self.assertEqual(r['context_method'], 'metadata_layer_pattern_fp16_with_25pct_margin')
        m['metadata']['attention']['full_attention_layers']=65
        self.assertEqual(self.evaluate(m)['rating'], 'UNKNOWN')  # more full layers than layers: abstain
    def test_sliding_window_cache_is_a_fixed_allowance(self):
        m=model(); m['metadata']['architecture']='gemma4'
        m['metadata']['attention'].update(full_attention_layers=4, window_cache_elements=100_000_000)
        r=self.evaluate(m); base=self.evaluate(model())
        self.assertEqual(r['fixed_cache_bytes'], int(100_000_000*2*1.25))
        self.assertEqual(r['runtime_reserve_bytes'], base['runtime_reserve_bytes']+r['fixed_cache_bytes'])
        m['metadata']['attention']['window_cache_elements']=-1
        self.assertEqual(self.evaluate(m)['rating'], 'UNKNOWN')
    def test_unpatterned_sliding_window_is_an_explicit_upper_bound(self):
        m=model(); m['metadata']['architecture']='gptoss'
        r=self.evaluate(m)
        self.assertEqual(r['kv_bytes_per_token_estimate'], int(32*8*256*2*1.25))
        self.assertTrue(r['context_method'].startswith('upper_bound_'))
    def test_gpt_oss_spelling_from_republished_gguf_is_the_same_upper_bound(self):
        m=model(); m['metadata']['architecture']='gpt-oss'
        self.assertTrue(self.evaluate(m)['context_method'].startswith('upper_bound_'))
    def test_unknown_architecture_still_abstains(self):
        m=model(); m['metadata']['architecture']='mamba-next'
        self.assertEqual(self.evaluate(m)['rating'], 'UNKNOWN')
    def test_missing_kv_abstains(self):
        m=model(); m['metadata']['attention']['kv_heads']=None
        self.assertIsNone(self.evaluate(m)['recommended_context'])
    def test_native_context_is_not_operating_context(self):
        self.assertEqual(self.evaluate(model())['recommended_context'],32768)
    def test_capacity_independent_of_load(self):
        p=profile(); before=fit.fit_response(p,[model()],[],True)['models'][0]['fit']
        p['dynamic']['memory']['available_bytes']=1
        loaded=fit.fit_response(p,[model()],[{'name':'arbitrary-label','size':9*G,'size_vram':9*G,'context_length':8192}],True)['models'][0]
        self.assertEqual(before,loaded['fit']); self.assertTrue(loaded['observed_runtime']['resident'])
    def test_cpu_only(self):
        r=self.evaluate(model(),profile(False)); self.assertEqual(r['gpu_offload'],'none')
        self.assertIsNotNone(r['recommended_context'])
    def test_multi_gpu_unknown(self):
        p=profile(); p['capacity']['gpus']*=2
        self.assertEqual(self.evaluate(model(),p)['rating'],'UNKNOWN')
    def test_unknown_gpu_does_not_fabricate_cpu_only(self):
        p=profile(); p['capacity']['gpus'][0]['vram_total_bytes']=None
        self.assertEqual(self.evaluate(model(),p)['rating'],'UNKNOWN')
    def test_integrated_or_virtual_graphics_use_system_memory(self):
        for gpu in ({'id':'gpu0','vendor':'intel','memory_kind':'unknown','vram_total_bytes':None},
                    {'id':'gpu0','vendor':'unknown','memory_kind':'unknown','vram_total_bytes':None},
                    {'id':'gpu0','vendor':'amd','memory_kind':'unknown','vram_total_bytes':512*1024**2}):
            p=profile(); p['capacity']['gpus']=[gpu]
            b=fit.operating_budget(p)
            self.assertEqual((b['mode'], b['graphics']), ('cpu', 'integrated_or_virtual'), gpu)
            self.assertIn('share system RAM', b['assumption'])
            self.assertIsNotNone(self.evaluate(model(), p)['recommended_context'])
    def test_sizeable_gpu_of_unknown_kind_stays_unknown(self):
        p=profile(); p['capacity']['gpus']=[{'id':'gpu0','vendor':'amd','memory_kind':'unknown','vram_total_bytes':16*G}]
        self.assertEqual(fit.operating_budget(p)['mode'], 'unknown')
    def test_dedicated_gpu_plus_integrated_keeps_gpu_budget(self):
        p=profile(); p['capacity']['gpus'].append({'id':'gpu1','vendor':'amd','memory_kind':'unknown','vram_total_bytes':512*1024**2})
        self.assertEqual(fit.operating_budget(p)['mode'], 'gpu_candidate')
    def test_too_large_poor(self): self.assertEqual(self.evaluate(model(40*G))['rating'],'POOR')
    def test_non_chat_unsupported(self):
        m=model(); m['capabilities']['chat']=False
        self.assertEqual(self.evaluate(m)['rating'],'UNSUPPORTED')
    def test_unknown_weights(self): self.assertEqual(self.evaluate(model(None))['rating'],'UNKNOWN')
    def test_small_gpu_budget_nonnegative(self):
        p=profile(); p['capacity']['gpus'][0]['vram_total_bytes']=G
        self.assertEqual(fit.operating_budget(p)['operating_budget_bytes'],0)
    def test_policy_configurable(self):
        self.assertEqual(fit.operating_budget(profile(),fit.HeadroomPolicy(gpu_reserve_fraction=.5))['operating_budget_bytes'],16*G)
    def test_gpu_explanation(self):
        self.assertEqual(fit.operating_budget(profile())['explanation'],
                         'NOVA keeps 4.8 GiB of your 32 GiB graphics memory free for the system, so models can use up to 27.2 GiB.')
    def test_cpu_explanation(self):
        p=profile(False); p['capacity']['memory']['total_bytes']=8*G
        self.assertEqual(fit.operating_budget(p)['explanation'],
                         "No graphics card was found, so models run in RAM. NOVA keeps 4 GiB for your desktop and apps, which leaves about 4 GiB for a model. Larger models are listed but can't be installed here.")
    def test_integrated_graphics_explanation(self):
        p=profile(False); p['capacity']['memory']['total_bytes']=16*G
        p['capacity']['gpus']=[{'id':'gpu0','vendor':'intel','memory_kind':'unknown','vram_total_bytes':None}]
        self.assertEqual(fit.operating_budget(p)['explanation'],
                         "This computer's graphics share its main memory, so models run in RAM. NOVA keeps 4 GiB for your desktop and apps, which leaves about 12 GiB for a model. Larger models are listed but can't be installed here.")
    def test_unknown_explanation(self):
        p=profile(); p['capacity']['gpus'][0]['vram_total_bytes']=None
        self.assertEqual(fit.operating_budget(p)['explanation'],
                         'NOVA could not measure your memory reliably, so it cannot say which models fit yet.')
    def test_cpu_unknown_memory_explanation(self):
        p=profile(False); p['capacity']['memory']['total_bytes']=None
        self.assertEqual(fit.operating_budget(p)['explanation'],
                         'NOVA could not measure your memory reliably, so it cannot say which models fit yet.')
    def test_explanation_formatting_whole_and_decimal_gib(self):
        gpu=fit.operating_budget(profile())['explanation']
        self.assertIn('32 GiB', gpu); self.assertIn('4.8 GiB', gpu); self.assertIn('27.2 GiB', gpu)
        self.assertNotIn('32.0 GiB', gpu)
        p=profile(False); p['capacity']['memory']['total_bytes']=8*G
        cpu=fit.operating_budget(p)['explanation']
        self.assertIn('4 GiB', cpu)
        self.assertNotIn('.0 GiB', cpu)
    def test_malformed_telemetry(self):
        for payload in ({}, {'gpus':None}, {'gpus':['bad']}, {'gpus':[{'memory_total_mib':'NaN','memory_used_mib':{}}]}):
            with patch.object(hardware,'telemetry',return_value=payload),patch.object(hardware,'storage_profile',return_value={}):
                p=hardware.collect_profile(False)
                json.dumps(p,allow_nan=False)
                self.assertFalse(p['local_ai']['available'])
    def test_provider_offline_partial_hardware(self):
        with patch.object(server,'collect_profile',side_effect=lambda available: dict(profile(),local_ai={'available':available})),patch.object(server,'PROVIDERS',{'ollama':OllamaProvider('http://unused','45m')}),patch.object(OllamaProvider,'models',side_effect=ProviderUnavailable()),patch.object(OllamaProvider,'running_models',side_effect=ProviderUnavailable()):
            result=server.local_model_fit()
            self.assertEqual(result['models'],[]); self.assertEqual(result['error'],'provider_unavailable')
            self.assertEqual(result['hardware']['capacity']['memory']['total_bytes'],32*G)
    def test_show_normalization_no_raw_strings(self):
        adapter=OllamaProvider('http://unused','45m')
        m=model(); m['local']=True
        details={'model_info': {'general.architecture':'qwen3','qwen3.context_length':40960,'qwen3.attention.head_count_kv':8},'capabilities':['completion','tools'],'template':'PRIVATE'}
        with patch.object(adapter,'models',return_value=[m]),patch.object(adapter,'_post_json',return_value=details):
            result=adapter.fit_models()[0]
            self.assertEqual(result['metadata']['context_length'],40960)
            self.assertNotIn('PRIVATE',json.dumps(result))
    def test_unknown_runtime_is_not_unloaded(self):
        r=fit.fit_response(profile(),[model()])['models'][0]['observed_runtime']
        self.assertIsNone(r['resident'])

class BoundaryTests(unittest.TestCase):
    def test_fixed_local_tools_reject_arguments(self):
        self.assertFalse(server.tool_result('get_local_model_fit',{'command':'anything'})[1]['ok'])
    def test_approval_single_use_and_expiry(self):
        store=server.ApprovalStore(); item=store.create('restart_novad',{'service':'novad.service'})
        self.assertEqual(store.decide(item.id,'denied')[0],202)
        self.assertEqual(store.decide(item.id,'approved')[0],409)
        item=store.create('restart_novad',{'service':'novad.service'}); item.created-=server.APPROVAL_TTL_SECONDS+1
        self.assertEqual(store.decide(item.id,'approved')[0],410)
    def test_denied_and_expired_agent_actions_never_execute(self):
        for decision in ('denied','expired'):
            responses=[{'role':'assistant','content':'','tool_calls':[{'function':{'name':'restart_nova_actions','arguments':{}}}]},
                       {'role':'assistant','content':'Action not executed.','tool_calls':[]}]
            with patch.object(server,'stream_model_turn',side_effect=responses), patch.object(server.APPROVALS,'wait',return_value=decision), patch.object(server,'execute_action') as execute, patch.object(server,'has_active_selection',return_value=True):
                self.assertEqual(server.run_agent_loop('test'),'Action not executed.')
                execute.assert_not_called()
        self.assertEqual(server.proposal('restart_novad',{'service':'arbitrary'})[1],'invalid_action_arguments')


if __name__=='__main__': unittest.main()
