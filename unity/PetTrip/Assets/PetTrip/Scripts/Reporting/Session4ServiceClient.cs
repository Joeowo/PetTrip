using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Networking;

namespace PetTrip
{
    /// <summary>
    /// 会话4：Unity -> 内容服务的回传客户端。
    /// - UploadSnapshotV2: 上传放置后的 v0.2 快照
    /// - UploadReport: 从 /snapshot-meta 取 run_id 与快照哈希，连同验证结果与截图
    ///   POST 回服务（报告哈希必须与服务端当前快照一致）。
    /// </summary>
    public sealed class Session4ServiceClient : MonoBehaviour
    {
        private const string DefaultBaseUrl = "http://127.0.0.1:8000";
        [SerializeField] private string baseUrl = DefaultBaseUrl;

        public string LastError { get; private set; }

        public IEnumerator UploadSnapshotV2(string runId, string snapshotJson, Action<bool> onComplete)
        {
            return PostJson(
                baseUrl + "/runs/" + runId + "/snapshot-v2",
                snapshotJson,
                expectStatus: 201,
                onComplete: onComplete);
        }

        public IEnumerator UploadReport(
            IReadOnlyList<(string name, bool passed, string detail)> checks,
            string screenshotPath,
            Action<bool> onComplete)
        {
            MetaInfo meta = null;
            yield return GetMeta(value => meta = value);
            if (meta == null)
            {
                onComplete?.Invoke(false);
                yield break;
            }

            string payload;
            try
            {
                payload = BuildReportJson(meta, checks, screenshotPath);
            }
            catch (Exception exception)
            {
                Fail("report build failed: " + exception.Message);
                onComplete?.Invoke(false);
                yield break;
            }
            yield return PostJson(
                baseUrl + "/runs/" + meta.run_id + "/reports",
                payload,
                expectStatus: 201,
                onComplete: onComplete);
        }

        private IEnumerator GetMeta(Action<MetaInfo> onComplete)
        {
            using (var request = UnityWebRequest.Get(baseUrl + "/snapshot-meta"))
            {
                request.timeout = 10;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    Fail("snapshot-meta fetch failed: " + request.error);
                    onComplete?.Invoke(null);
                    yield break;
                }
                var meta = JsonUtility.FromJson<MetaInfo>(request.downloadHandler.text);
                onComplete?.Invoke(meta);
            }
        }

        private static string BuildReportJson(
            MetaInfo meta,
            IReadOnlyList<(string name, bool passed, string detail)> checks,
            string screenshotPath)
        {
            var screenshotBase64 = Convert.ToBase64String(File.ReadAllBytes(screenshotPath));
            var parts = new List<string>(checks.Count);
            foreach (var check in checks)
            {
                parts.Add(
                    "{\"name\":\"" + check.name + "\",\"passed\":" + (check.passed ? "true" : "false")
                    + ",\"detail\":\"" + check.detail + "\"}");
            }
            return "{\"snapshot_sha256\":\"" + meta.sha256
                   + "\",\"checks\":[" + string.Join(",", parts)
                   + "],\"screenshot_png_base64\":\"" + screenshotBase64 + "\"}";
        }

        private IEnumerator PostJson(string url, string json, int expectStatus, Action<bool> onComplete)
        {
            using (var request = new UnityWebRequest(url, "POST"))
            {
                request.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(json));
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                request.timeout = 30;
                yield return request.SendWebRequest();
                if (request.result != UnityWebRequest.Result.Success)
                {
                    Fail("POST failed (" + request.responseCode + "): " + request.downloadHandler.text);
                    onComplete?.Invoke(false);
                    yield break;
                }
                if ((int)request.responseCode != expectStatus)
                {
                    Fail("unexpected status " + request.responseCode + ": " + request.downloadHandler.text);
                    onComplete?.Invoke(false);
                    yield break;
                }
                Debug.Log("PETTRIP_SESSION4_POST_OK url=" + url);
                onComplete?.Invoke(true);
            }
        }

        private void Fail(string reason)
        {
            LastError = reason;
            Debug.LogError("PETTRIP_SESSION4_POST_FAILED reason=" + reason);
        }

        [Serializable]
        public sealed class MetaInfo
        {
            public string run_id;
            public string snapshot;
            public string schema_version;
            public string sha256;
        }
    }
}
