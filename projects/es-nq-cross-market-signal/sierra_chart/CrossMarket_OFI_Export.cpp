#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <windows.h>
#endif

#include "sierrachart.h"
SCDLLName("CrossMarket_OFI_Export")

// ======================================================================
// Cross-Market OFI Export (ACSIL) for standalone es-nq-cross-market-signal
// ======================================================================
// Exports OFI-style depth snapshots as JSON lines for:
// ES, NQ, YM, RTY, E6, ZN, ZB, CL, GC
//
// Example:
// {"ts":"2026-05-19T23:05:01.123Z","symbol":"F.US.EPM26","ofi":12.34,
//  "spread":0.25,"bid_depth":510.0,"ask_depth":480.0,"spot":5932.25,
//  "bar_bid_volume":1388,"bar_ask_volume":1118,"bar_delta":270}
// ======================================================================

#include <algorithm>
#include <chrono>
#include <cctype>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

namespace {

#ifdef _WIN32
struct BroadcastServerState {
    bool winsock_started = false;
    SOCKET listen_socket = INVALID_SOCKET;
    std::vector<SOCKET> clients;
    std::string bind_host;
    int bind_port = 0;
    int active_study_instances = 0;
    int owner_chart_number = 0;
    int owner_study_graph_instance_id = 0;
};

BroadcastServerState g_server;

struct DynamicWinSockApi {
    HMODULE dll = nullptr;

    int(WSAAPI* p_WSAStartup)(WORD, LPWSADATA) = nullptr;
    int(WSAAPI* p_WSACleanup)(void) = nullptr;
    SOCKET(WSAAPI* p_socket)(int, int, int) = nullptr;
    int(WSAAPI* p_closesocket)(SOCKET) = nullptr;
    int(WSAAPI* p_shutdown)(SOCKET, int) = nullptr;
    int(WSAAPI* p_setsockopt)(SOCKET, int, int, const char*, int) = nullptr;
    int(WSAAPI* p_ioctlsocket)(SOCKET, long, u_long*) = nullptr;
    unsigned long(WSAAPI* p_inet_addr)(const char*) = nullptr;
    int(WSAAPI* p_bind)(SOCKET, const sockaddr*, int) = nullptr;
    int(WSAAPI* p_listen)(SOCKET, int) = nullptr;
    SOCKET(WSAAPI* p_accept)(SOCKET, sockaddr*, int*) = nullptr;
    int(WSAAPI* p_send)(SOCKET, const char*, int, int) = nullptr;
    int(WSAAPI* p_WSAGetLastError)(void) = nullptr;
};

DynamicWinSockApi g_winsock;
#endif

double NowEpochSeconds() {
    return std::chrono::duration<double>(
        std::chrono::system_clock::now().time_since_epoch()
    )
        .count();
}

std::string ToUpper(std::string value) {
    for (char& ch : value) {
        ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
    }
    return value;
}

bool ContainsText(const std::string& text, const std::string& token) {
    if (token.empty()) {
        return false;
    }
    return text.find(token) != std::string::npos;
}

std::vector<std::string> SplitCsvUpperTokens(const std::string& csv_upper) {
    std::vector<std::string> tokens;
    std::string current;
    for (char ch : csv_upper) {
        if (ch == ',') {
            if (!current.empty()) {
                tokens.push_back(current);
                current.clear();
            }
            continue;
        }
        if (std::isspace(static_cast<unsigned char>(ch)) != 0) {
            continue;
        }
        current.push_back(ch);
    }
    if (!current.empty()) {
        tokens.push_back(current);
    }
    return tokens;
}

std::string DetectMarketFromSymbol(const std::string& symbol_upper) {
    // ES / NQ
    if (ContainsText(symbol_upper, "ENQ") || ContainsText(symbol_upper, "NQ")) {
        return "NQ";
    }
    // CQG root for ES is EP (ex: F.US.EPM26), some feeds use ES.
    if (ContainsText(symbol_upper, "EPM") || ContainsText(symbol_upper, ".EP") ||
        ContainsText(symbol_upper, " ES")) {
        return "ES";
    }
    if (ContainsText(symbol_upper, "ES") && !ContainsText(symbol_upper, "MES")) {
        return "ES";
    }
    // YM / RTY (RTY check must come first because RTYM contains "YM")
    if (ContainsText(symbol_upper, "RTY") && !ContainsText(symbol_upper, "M2K")) {
        return "RTY";
    }
    if (ContainsText(symbol_upper, "YM") && !ContainsText(symbol_upper, "MYM")) {
        return "YM";
    }
    // Rates and FX
    if (ContainsText(symbol_upper, "ZN") || ContainsText(symbol_upper, "TYAU")) {
        return "ZN";
    }
    if (ContainsText(symbol_upper, "ZB") || ContainsText(symbol_upper, "USAU")) {
        return "ZB";
    }
    if (ContainsText(symbol_upper, "E6") || ContainsText(symbol_upper, "6E") ||
        ContainsText(symbol_upper, "EURUSD")) {
        return "E6";
    }
    // Commodities
    if (ContainsText(symbol_upper, "CL")) {
        return "CL";
    }
    if (ContainsText(symbol_upper, "GC")) {
        return "GC";
    }
    if (ContainsText(symbol_upper, "VXM") || ContainsText(symbol_upper, " VX") ||
        symbol_upper.rfind("VX", 0) == 0) {
        return "VX";
    }
    return "";
}

std::string IsoUtcNowMillis() {
    using namespace std::chrono;
    const auto now = system_clock::now();
    const auto secs = time_point_cast<seconds>(now);
    const auto millis = duration_cast<milliseconds>(now - secs).count();
    const std::time_t tt = system_clock::to_time_t(now);

    std::tm tm_utc {};
#ifdef _WIN32
    gmtime_s(&tm_utc, &tt);
#else
    gmtime_r(&tt, &tm_utc);
#endif

    std::ostringstream oss;
    oss << std::put_time(&tm_utc, "%Y-%m-%dT%H:%M:%S") << "."
        << std::setw(3) << std::setfill('0') << millis << "Z";
    return oss.str();
}

void LogMessage(SCStudyInterfaceRef sc, const std::string& text, int show_alert = 0) {
    SCString msg(text.c_str());
    sc.AddMessageToLog(msg, show_alert);
}

void LogThrottled(
    SCStudyInterfaceRef sc,
    int persistent_key,
    const std::string& text,
    int show_alert = 0,
    double min_interval_seconds = 30.0
) {
    double& last = sc.GetPersistentDouble(persistent_key);
    const double now = NowEpochSeconds();
    if (last > 0.0 && (now - last) < min_interval_seconds) {
        return;
    }
    last = now;
    LogMessage(sc, text, show_alert);
}

bool IsSymbolAllowed(
    const std::string& symbol_upper,
    const std::string& symbol_filters_csv_upper
) {
    if (symbol_filters_csv_upper.empty()) {
        return true;
    }
    const std::vector<std::string> filters = SplitCsvUpperTokens(symbol_filters_csv_upper);
    for (const std::string& token : filters) {
        if (ContainsText(symbol_upper, token)) {
            return true;
        }
    }
    return false;
}

bool ReadDepthSnapshot(
    SCStudyInterfaceRef sc,
    int levels_to_aggregate,
    double& best_bid,
    double& best_ask,
    double& bid_depth,
    double& ask_depth
) {
    best_bid = 0.0;
    best_ask = 0.0;
    bid_depth = 0.0;
    ask_depth = 0.0;

    s_MarketDepthEntry top_bid {};
    s_MarketDepthEntry top_ask {};
    if (sc.GetBidMarketDepthEntryAtLevel(top_bid, 0) == 0 ||
        sc.GetAskMarketDepthEntryAtLevel(top_ask, 0) == 0) {
        return false;
    }

    best_bid = top_bid.Price;
    best_ask = top_ask.Price;

    const int levels = (std::max)(1, levels_to_aggregate);
    for (int level = 0; level < levels; ++level) {
        s_MarketDepthEntry bid_entry {};
        s_MarketDepthEntry ask_entry {};
        if (sc.GetBidMarketDepthEntryAtLevel(bid_entry, level) != 0) {
            bid_depth += static_cast<double>(bid_entry.Quantity);
        }
        if (sc.GetAskMarketDepthEntryAtLevel(ask_entry, level) != 0) {
            ask_depth += static_cast<double>(ask_entry.Quantity);
        }
    }
    return true;
}

std::string BuildJsonLine(
    const std::string& ts_utc,
    const std::string& symbol,
    const std::string& market,
    double ofi,
    double spread,
    double bid_depth,
    double ask_depth,
    double spot,
    double bar_bid_volume,
    double bar_ask_volume,
    double bar_delta_bid_minus_ask,
    double bar_delta_rolling_5,
    double bar_delta_rolling_10,
    double bar_delta_cumulative
) {
    std::ostringstream oss;
    oss.setf(std::ios::fixed);
    oss << std::setprecision(6);
    oss << "{\"ts\":\"" << ts_utc << "\""
        << ",\"symbol\":\"" << symbol << "\"";
    if (!market.empty()) {
        oss << ",\"market\":\"" << market << "\"";
    }
    oss << ",\"ofi\":" << ofi
        << ",\"spread\":" << spread
        << ",\"bid_depth\":" << bid_depth
        << ",\"ask_depth\":" << ask_depth
        << ",\"spot\":" << spot
        << ",\"bar_bid_volume\":" << bar_bid_volume
        << ",\"bar_ask_volume\":" << bar_ask_volume
        << ",\"bar_delta\":" << bar_delta_bid_minus_ask
        << ",\"bar_delta_bid_minus_ask\":" << bar_delta_bid_minus_ask
        << ",\"bar_delta_ask_minus_bid\":" << -bar_delta_bid_minus_ask
        << ",\"bar_delta_rolling_5\":" << bar_delta_rolling_5
        << ",\"bar_delta_rolling_10\":" << bar_delta_rolling_10
        << ",\"bar_delta_cumulative\":" << bar_delta_cumulative
        << "}\n";
    return oss.str();
}

bool AppendJsonl(const std::string& file_path, const std::string& line) {
    if (file_path.empty()) {
        return false;
    }
    std::ofstream out(file_path.c_str(), std::ios::out | std::ios::app);
    if (!out.is_open()) {
        return false;
    }
    out << line;
    return true;
}

double BarBidVolume(SCStudyInterfaceRef sc, int index) {
    if (index < 0 || index >= sc.ArraySize) {
        return 0.0;
    }
    return static_cast<double>(sc.BaseData[SC_BIDVOL][index]);
}

double BarAskVolume(SCStudyInterfaceRef sc, int index) {
    if (index < 0 || index >= sc.ArraySize) {
        return 0.0;
    }
    return static_cast<double>(sc.BaseData[SC_ASKVOL][index]);
}

double BarDeltaBidMinusAsk(SCStudyInterfaceRef sc, int index) {
    return BarBidVolume(sc, index) - BarAskVolume(sc, index);
}

double RollingBarDeltaBidMinusAsk(SCStudyInterfaceRef sc, int last_index, int bars) {
    if (last_index < 0 || bars <= 0) {
        return 0.0;
    }
    const int first_index = (std::max)(0, last_index - bars + 1);
    double total = 0.0;
    for (int index = first_index; index <= last_index; ++index) {
        total += BarDeltaBidMinusAsk(sc, index);
    }
    return total;
}

double CumulativeBarDeltaBidMinusAsk(SCStudyInterfaceRef sc, int last_index) {
    if (last_index < 0) {
        return 0.0;
    }
    double total = 0.0;
    for (int index = 0; index <= last_index; ++index) {
        total += BarDeltaBidMinusAsk(sc, index);
    }
    return total;
}

#ifdef _WIN32
bool LoadWinSockApi() {
    if (g_winsock.dll != nullptr) {
        return true;
    }

    g_winsock.dll = ::LoadLibraryA("ws2_32.dll");
    if (g_winsock.dll == nullptr) {
        return false;
    }

#define LOAD_WINSOCK_PROC(field, name)                                                              \
    g_winsock.field = reinterpret_cast<decltype(g_winsock.field)>(::GetProcAddress(g_winsock.dll, name)); \
    if (g_winsock.field == nullptr) {                                                                \
        ::FreeLibrary(g_winsock.dll);                                                                \
        g_winsock = DynamicWinSockApi {};                                                            \
        return false;                                                                                \
    }

    LOAD_WINSOCK_PROC(p_WSAStartup, "WSAStartup");
    LOAD_WINSOCK_PROC(p_WSACleanup, "WSACleanup");
    LOAD_WINSOCK_PROC(p_socket, "socket");
    LOAD_WINSOCK_PROC(p_closesocket, "closesocket");
    LOAD_WINSOCK_PROC(p_shutdown, "shutdown");
    LOAD_WINSOCK_PROC(p_setsockopt, "setsockopt");
    LOAD_WINSOCK_PROC(p_ioctlsocket, "ioctlsocket");
    LOAD_WINSOCK_PROC(p_inet_addr, "inet_addr");
    LOAD_WINSOCK_PROC(p_bind, "bind");
    LOAD_WINSOCK_PROC(p_listen, "listen");
    LOAD_WINSOCK_PROC(p_accept, "accept");
    LOAD_WINSOCK_PROC(p_send, "send");
    LOAD_WINSOCK_PROC(p_WSAGetLastError, "WSAGetLastError");

#undef LOAD_WINSOCK_PROC
    return true;
}

u_short HostToNetwork16(u_short value) {
    return static_cast<u_short>(((value & 0x00FFu) << 8) | ((value & 0xFF00u) >> 8));
}

void CloseSocketSafe(SOCKET sock) {
    if (sock == INVALID_SOCKET) {
        return;
    }
    if (g_winsock.p_shutdown != nullptr) {
        g_winsock.p_shutdown(sock, SD_BOTH);
    }
    if (g_winsock.p_closesocket != nullptr) {
        g_winsock.p_closesocket(sock);
    }
}

void CloseAllClients() {
    for (SOCKET sock : g_server.clients) {
        CloseSocketSafe(sock);
    }
    g_server.clients.clear();
}

void ResetServerSocketsOnly() {
    CloseAllClients();
    if (g_server.listen_socket != INVALID_SOCKET) {
        if (g_winsock.p_closesocket != nullptr) {
            g_winsock.p_closesocket(g_server.listen_socket);
        }
        g_server.listen_socket = INVALID_SOCKET;
    }
    g_server.bind_host.clear();
    g_server.bind_port = 0;
}

void CleanupServerFully() {
    ResetServerSocketsOnly();
    if (g_server.winsock_started && g_winsock.p_WSACleanup != nullptr) {
        g_winsock.p_WSACleanup();
        g_server.winsock_started = false;
    }
    if (g_winsock.dll != nullptr) {
        ::FreeLibrary(g_winsock.dll);
        g_winsock = DynamicWinSockApi {};
    }
}

bool AcquireServerOwnership(SCStudyInterfaceRef sc) {
    if (g_server.owner_study_graph_instance_id == 0) {
        g_server.owner_chart_number = sc.ChartNumber;
        g_server.owner_study_graph_instance_id = sc.StudyGraphInstanceID;
        return true;
    }

    if (g_server.owner_study_graph_instance_id == sc.StudyGraphInstanceID) {
        return true;
    }

    return false;
}

void ReleaseServerOwnershipIfOwner(SCStudyInterfaceRef sc) {
    if (g_server.owner_study_graph_instance_id != sc.StudyGraphInstanceID) {
        return;
    }
    g_server.owner_chart_number = 0;
    g_server.owner_study_graph_instance_id = 0;
}

void ForceReleaseServerForStudy(SCStudyInterfaceRef sc) {
    if (g_server.owner_study_graph_instance_id != sc.StudyGraphInstanceID) {
        return;
    }

    ReleaseServerOwnershipIfOwner(sc);
    ResetServerSocketsOnly();
    if (g_server.winsock_started && g_winsock.p_WSACleanup != nullptr) {
        g_winsock.p_WSACleanup();
        g_server.winsock_started = false;
    }
    if (g_winsock.dll != nullptr) {
        ::FreeLibrary(g_winsock.dll);
        g_winsock = DynamicWinSockApi {};
    }
}

bool EnsureServerListening(
    SCStudyInterfaceRef sc,
    const std::string& bind_host,
    int bind_port,
    bool log_errors
) {
    if (bind_port <= 0 || bind_port > 65535) {
        if (log_errors) {
            LogThrottled(sc, 9101, "Cross-Market OFI Export: invalid TCP port.", 0, 30.0);
        }
        return false;
    }

    std::string host = bind_host.empty() ? "127.0.0.1" : bind_host;

    if (g_server.listen_socket != INVALID_SOCKET && g_server.bind_host == host &&
        g_server.bind_port == bind_port) {
        return true;
    }

    // Rebind when host/port changed.
    ResetServerSocketsOnly();

    if (!LoadWinSockApi()) {
        if (log_errors) {
            LogThrottled(sc, 9102, "Cross-Market OFI Export: failed to load ws2_32.dll API.", 0, 30.0);
        }
        return false;
    }

    if (!g_server.winsock_started) {
        WSADATA wsa_data {};
        if (g_winsock.p_WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
            if (log_errors) {
                LogThrottled(sc, 9102, "Cross-Market OFI Export: WSAStartup failed.", 0, 30.0);
            }
            return false;
        }
        g_server.winsock_started = true;
    }

    SOCKET listen_sock = g_winsock.p_socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listen_sock == INVALID_SOCKET) {
        if (log_errors) {
            LogThrottled(sc, 9103, "Cross-Market OFI Export: failed to create listen socket.", 0, 30.0);
        }
        return false;
    }

    // Reuse address so restarts can rebind quickly.
    int reuse_addr = 1;
    g_winsock.p_setsockopt(
        listen_sock,
        SOL_SOCKET,
        SO_REUSEADDR,
        reinterpret_cast<const char*>(&reuse_addr),
        sizeof(reuse_addr)
    );

    // Non-blocking listen socket so accept loop never blocks chart updates.
    u_long non_blocking = 1;
    g_winsock.p_ioctlsocket(listen_sock, FIONBIO, &non_blocking);

    sockaddr_in addr {};
    addr.sin_family = AF_INET;
    addr.sin_port = HostToNetwork16(static_cast<u_short>(bind_port));
    const unsigned long host_addr = g_winsock.p_inet_addr(host.c_str());
    if (host_addr == INADDR_NONE) {
        g_winsock.p_closesocket(listen_sock);
        if (log_errors) {
            LogThrottled(sc, 9104, "Cross-Market OFI Export: invalid bind host (use IPv4 like 127.0.0.1).", 0, 30.0);
        }
        return false;
    }
    addr.sin_addr.s_addr = host_addr;

    if (g_winsock.p_bind(listen_sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR) {
        g_winsock.p_closesocket(listen_sock);
        if (log_errors) {
            LogThrottled(sc, 9105, "Cross-Market OFI Export: bind failed (port may already be in use).", 0, 30.0);
        }
        return false;
    }

    if (g_winsock.p_listen(listen_sock, 8) == SOCKET_ERROR) {
        g_winsock.p_closesocket(listen_sock);
        if (log_errors) {
            LogThrottled(sc, 9106, "Cross-Market OFI Export: listen failed.", 0, 30.0);
        }
        return false;
    }

    g_server.listen_socket = listen_sock;
    g_server.bind_host = host;
    g_server.bind_port = bind_port;

    SCString log_line;
    log_line.Format("Cross-Market OFI Export: listening on %s:%d", host.c_str(), bind_port);
    sc.AddMessageToLog(log_line, 0);

    return true;
}

void AcceptPendingClients() {
    if (g_server.listen_socket == INVALID_SOCKET) {
        return;
    }

    while (true) {
        sockaddr_in client_addr {};
        int client_len = sizeof(client_addr);
        SOCKET client = g_winsock.p_accept(
            g_server.listen_socket,
            reinterpret_cast<sockaddr*>(&client_addr),
            &client_len
        );
        if (client == INVALID_SOCKET) {
            const int err = g_winsock.p_WSAGetLastError();
            if (err == WSAEWOULDBLOCK) {
                break;
            }
            break;
        }

        // Use blocking sends for tiny JSON lines, but cap timeout tightly.
        u_long blocking_mode = 0;
        g_winsock.p_ioctlsocket(client, FIONBIO, &blocking_mode);
        int send_timeout_ms = 50;
        g_winsock.p_setsockopt(
            client,
            SOL_SOCKET,
            SO_SNDTIMEO,
            reinterpret_cast<const char*>(&send_timeout_ms),
            sizeof(send_timeout_ms)
        );
        g_server.clients.push_back(client);
    }
}

int BroadcastLineToClients(const std::string& line) {
    if (g_server.listen_socket == INVALID_SOCKET) {
        return 0;
    }

    AcceptPendingClients();
    if (g_server.clients.empty()) {
        return 0;
    }

    int delivered = 0;
    for (size_t i = 0; i < g_server.clients.size();) {
        SOCKET client = g_server.clients[i];
        bool ok = true;
        size_t offset = 0;
        while (offset < line.size()) {
            const int sent = g_winsock.p_send(
                client,
                line.c_str() + offset,
                static_cast<int>(line.size() - offset),
                0
            );
            if (sent <= 0) {
                ok = false;
                break;
            }
            offset += static_cast<size_t>(sent);
        }

        if (ok) {
            ++delivered;
            ++i;
            continue;
        }

        CloseSocketSafe(client);
        g_server.clients.erase(
            g_server.clients.begin() + static_cast<std::vector<SOCKET>::difference_type>(i)
        );
    }

    return delivered;
}
#endif

} // namespace

SCSFExport scsf_CrossMarketOFIExport(SCStudyInterfaceRef sc) {
    SCInputRef In_EnableTcpServer = sc.Input[0];
    SCInputRef In_BindHost = sc.Input[1];
    SCInputRef In_BindPort = sc.Input[2];
    SCInputRef In_EnableFileAppend = sc.Input[3];
    SCInputRef In_FilePath = sc.Input[4];
    SCInputRef In_LevelsToAggregate = sc.Input[5];
    SCInputRef In_EmitIntervalSeconds = sc.Input[6];
    SCInputRef In_SymbolFilters = sc.Input[7];
    SCInputRef In_AllowUnknownMarket = sc.Input[8];
    SCInputRef In_LogErrors = sc.Input[9];

    if (sc.SetDefaults) {
        sc.GraphName = "Cross-Market OFI Export";
        sc.StudyDescription =
            "Hosts a TCP JSON-line stream of cross-market OFI depth snapshots for a standalone ES/NQ signal stack.";
        sc.AutoLoop = 0;
        sc.GraphRegion = 0;
        sc.UpdateAlways = 1;
        sc.UsesMarketDepthData = 1;
        sc.MaintainAdditionalChartDataArrays = 1;

        In_EnableTcpServer.Name = "Enable TCP Server Stream";
        In_EnableTcpServer.SetYesNo(1);

        In_BindHost.Name = "Bind Host";
        In_BindHost.SetString("127.0.0.1");

        In_BindPort.Name = "Bind Port";
        In_BindPort.SetInt(5562);
        In_BindPort.SetIntLimits(1, 65535);

        In_EnableFileAppend.Name = "Enable JSONL File Append";
        In_EnableFileAppend.SetYesNo(0);

        In_FilePath.Name = "JSONL File Path";
        In_FilePath.SetString("C:\\SierraChart\\Data\\CrossMarket_OFI_1s.jsonl");

        In_LevelsToAggregate.Name = "Levels To Aggregate";
        In_LevelsToAggregate.SetInt(5);
        In_LevelsToAggregate.SetIntLimits(1, 30);

        In_EmitIntervalSeconds.Name = "Emit Interval Seconds";
        In_EmitIntervalSeconds.SetFloat(1.0f);
        In_EmitIntervalSeconds.SetFloatLimits(0.1f, 60.0f);

        In_SymbolFilters.Name = "Allowed Symbol Tokens CSV (contains)";
        In_SymbolFilters.SetString("EPM,ENQ,YM,RTY,E6,6E,EURUSD,ZN,TYAU,ZB,USAU,CL,GC,VX,VXM");

        In_AllowUnknownMarket.Name = "Allow Unknown Market Label";
        In_AllowUnknownMarket.SetYesNo(0);

        In_LogErrors.Name = "Log Errors";
        In_LogErrors.SetYesNo(1);

        return;
    }

    int& is_registered = sc.GetPersistentInt(6001);
    if (is_registered == 0) {
        is_registered = 1;
#ifdef _WIN32
        ++g_server.active_study_instances;
#endif
    }

    if (sc.LastCallToFunction) {
        if (is_registered != 0) {
            is_registered = 0;
#ifdef _WIN32
            const bool was_owner = (g_server.owner_study_graph_instance_id == sc.StudyGraphInstanceID);
            --g_server.active_study_instances;
            if (g_server.active_study_instances <= 0) {
                g_server.active_study_instances = 0;
                CleanupServerFully();
            } else if (was_owner) {
                // Owner study removed while other instances still exist.
                // Drop sockets so one of the remaining studies can claim ownership.
                ReleaseServerOwnershipIfOwner(sc);
                ResetServerSocketsOnly();
                if (g_server.winsock_started && g_winsock.p_WSACleanup != nullptr) {
                    g_winsock.p_WSACleanup();
                    g_server.winsock_started = false;
                }
                if (g_winsock.dll != nullptr) {
                    ::FreeLibrary(g_winsock.dll);
                    g_winsock = DynamicWinSockApi {};
                }
            }
#endif
        }
        return;
    }

    const std::string symbol_upper = ToUpper(sc.Symbol.GetChars());
    const std::string symbol_filters_upper = ToUpper(In_SymbolFilters.GetString());
    if (!IsSymbolAllowed(symbol_upper, symbol_filters_upper)) {
#ifdef _WIN32
        if (In_EnableTcpServer.GetYesNo() == 0) {
            ForceReleaseServerForStudy(sc);
        }
#endif
        return;
    }

#ifdef _WIN32
    if (In_EnableTcpServer.GetYesNo() == 0) {
        ForceReleaseServerForStudy(sc);
    }
#endif

    bool tcp_server_ready = false;
    if (In_EnableTcpServer.GetYesNo() != 0) {
#ifdef _WIN32
        const bool is_owner = AcquireServerOwnership(sc);
        if (is_owner) {
            const std::string bind_host = In_BindHost.GetString();
            const int bind_port = In_BindPort.GetInt();
            tcp_server_ready = EnsureServerListening(sc, bind_host, bind_port, In_LogErrors.GetYesNo() != 0);
        } else {
            // Non-owner instances still publish through the shared listener.
            tcp_server_ready = (g_server.listen_socket != INVALID_SOCKET);
            if (!tcp_server_ready && In_LogErrors.GetYesNo() != 0) {
                LogThrottled(
                    sc,
                    6010,
                    "Cross-Market OFI Export: waiting for socket-owner study to initialize listener.",
                    0,
                    30.0
                );
            }
        }
#else
        if (In_LogErrors.GetYesNo() != 0) {
            LogThrottled(sc, 6008, "Cross-Market OFI Export: TCP server not implemented on this platform build.", 0, 60.0);
        }
#endif
    }

    const double now_epoch = NowEpochSeconds();
    double& last_emit_epoch = sc.GetPersistentDouble(6002);
    const double emit_interval = (std::max)(0.1f, In_EmitIntervalSeconds.GetFloat());
    if (last_emit_epoch > 0.0 && (now_epoch - last_emit_epoch) < emit_interval) {
        return;
    }

    double best_bid = 0.0;
    double best_ask = 0.0;
    double bid_depth = 0.0;
    double ask_depth = 0.0;
    const bool has_depth = ReadDepthSnapshot(
        sc,
        In_LevelsToAggregate.GetInt(),
        best_bid,
        best_ask,
        bid_depth,
        ask_depth
    );
    if (!has_depth) {
        if (In_LogErrors.GetYesNo() != 0) {
            LogThrottled(
                sc,
                6003,
                "Cross-Market OFI Export: no market depth yet for this symbol.",
                0,
                30.0
            );
        }
        return;
    }

    const double spread = best_ask - best_bid;
    const double spot = (best_bid + best_ask) * 0.5;
    const int latest_bar_index = sc.ArraySize > 0 ? sc.ArraySize - 1 : -1;
    const double bar_bid_volume = BarBidVolume(sc, latest_bar_index);
    const double bar_ask_volume = BarAskVolume(sc, latest_bar_index);
    const double bar_delta_bid_minus_ask = bar_bid_volume - bar_ask_volume;
    const double bar_delta_rolling_5 = RollingBarDeltaBidMinusAsk(sc, latest_bar_index, 5);
    const double bar_delta_rolling_10 = RollingBarDeltaBidMinusAsk(sc, latest_bar_index, 10);
    const double bar_delta_cumulative = CumulativeBarDeltaBidMinusAsk(sc, latest_bar_index);

    double& prev_bid_depth = sc.GetPersistentDouble(6004);
    double& prev_ask_depth = sc.GetPersistentDouble(6005);
    int& has_prev_depth = sc.GetPersistentInt(6006);
    double ofi = 0.0;
    if (has_prev_depth != 0) {
        ofi = (bid_depth - prev_bid_depth) - (ask_depth - prev_ask_depth);
    } else {
        has_prev_depth = 1;
    }
    prev_bid_depth = bid_depth;
    prev_ask_depth = ask_depth;

    const std::string market = DetectMarketFromSymbol(symbol_upper);
    if (market.empty() && In_AllowUnknownMarket.GetYesNo() == 0) {
        if (In_LogErrors.GetYesNo() != 0) {
            LogThrottled(
                sc,
                6009,
                "Cross-Market OFI Export: symbol did not map to known market; skipping row.",
                0,
                30.0
            );
        }
        return;
    }
    const std::string market_out = market.empty() ? "UNKNOWN" : market;
    const std::string json_line = BuildJsonLine(
        IsoUtcNowMillis(),
        symbol_upper,
        market_out,
        ofi,
        spread,
        bid_depth,
        ask_depth,
        spot,
        bar_bid_volume,
        bar_ask_volume,
        bar_delta_bid_minus_ask,
        bar_delta_rolling_5,
        bar_delta_rolling_10,
        bar_delta_cumulative
    );

    if (In_EnableFileAppend.GetYesNo() != 0) {
        const std::string file_path = In_FilePath.GetString();
        const bool wrote = AppendJsonl(file_path, json_line);
        if (!wrote && In_LogErrors.GetYesNo() != 0) {
            LogThrottled(sc, 6007, "Cross-Market OFI Export: failed to append JSONL file.", 0, 30.0);
        }
    }

    if (In_EnableTcpServer.GetYesNo() != 0) {
#ifdef _WIN32
        if (tcp_server_ready) {
            (void)BroadcastLineToClients(json_line);
        }
#else
        if (In_LogErrors.GetYesNo() != 0) {
            LogThrottled(sc, 6008, "Cross-Market OFI Export: TCP server not implemented on this platform build.", 0, 60.0);
        }
#endif
    }

    last_emit_epoch = now_epoch;
}

