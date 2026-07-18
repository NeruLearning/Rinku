using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

public class NeuroSDK : IDisposable
{
    private ClientWebSocket _webSocket;
    private CancellationTokenSource _cts;
    private bool _isConnected = false;

    // Das wichtigste Event: Hier klinkt sich das Spiel ein, 
    // um auf Befehle (Actions) von deinem Rust-Server zu hören.
    public event Action<string> OnActionReceived;

    // Event für Verbindungsstatus
    public event Action OnConnected;
    public event Action OnDisconnected;

    /// <summary>
    /// Verbindet das SDK mit deinem Rust-Server.
    /// </summary>
    /// <param name="serverUrl">z.B. "ws://localhost:8080"</param>
    public async Task ConnectAsync(string serverUrl)
    {
        if (_isConnected) return;

        _webSocket = new ClientWebSocket();
        _cts = new CancellationTokenSource();

        try
        {
            await _webSocket.ConnectAsync(new Uri(serverUrl), _cts.Token);
            _isConnected = true;
            OnConnected?.Invoke();

            // Starte den permanenten Hintergrund-Hörer für eingehende Actions
            _ = ReceiveLoopAsync();
        }
        catch (Exception ex)
        {
            UnityEngine.Debug.LogError($"[NeuroSDK] Verbindung fehlgeschlagen: {ex.Message}");
            _isConnected = false;
        }
    }

    /// <summary>
    /// Schickt JEDEN beliebigen JSON-Zustand des Spiels an den Rust-Server.
    /// </summary>
    public async Task SendContextAsync(string jsonContext)
    {
        if (!_isConnected || _webSocket.State != WebSocketState.Open) return;

        try
        {
            byte[] bytes = Encoding.UTF8.GetBytes(jsonContext);
            var segment = new ArraySegment<byte>(bytes);
            await _webSocket.SendAsync(segment, WebSocketMessageType.Text, true, _cts.Token);
        }
        catch (Exception ex)
        {
            UnityEngine.Debug.LogError($"[NeuroSDK] Fehler beim Senden: {ex.Message}");
        }
    }

    // Der Hintergrund-Thread, der dauerhaft auf den Rust-Server lauscht
    private async Task ReceiveLoopAsync()
    {
        byte[] buffer = new byte[1024 * 8]; // 8KB Buffer für größere Prompts/Actions

        while (_isConnected && _webSocket.State == WebSocketState.Open)
        {
            try
            {
                var result = await _webSocket.ReceiveAsync(new ArraySegment<byte>(buffer), _cts.Token);

                if (result.MessageType == WebSocketMessageType.Close)
                {
                    await DisconnectAsync();
                    break;
                }

                if (result.MessageType == WebSocketMessageType.Text)
                {
                    string rawJsonAction = Encoding.UTF8.GetString(buffer, 0, result.Count);

                    // Schmeiße das rohe JSON-Paket per Event ins Spiel
                    OnActionReceived?.Invoke(rawJsonAction);
                }
            }
            catch (Exception ex)
            {
                UnityEngine.Debug.LogError($"[NeuroSDK] Fehler beim Empfangen: {ex.Message}");
                break;
            }
        }
    }

    public async Task DisconnectAsync()
    {
        if (!_isConnected) return;

        _cts?.Cancel();
        if (_webSocket != null && _webSocket.State == WebSocketState.Open)
        {
            await _webSocket.CloseAsync(WebSocketCloseStatus.NormalClosure, "Closing", CancellationToken.None);
        }

        _isConnected = false;
        OnDisconnected?.Invoke();
        _webSocket?.Dispose();
    }

    public void Dispose()
    {
        _ = DisconnectAsync();
    }
}