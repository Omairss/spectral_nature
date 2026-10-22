import Foundation

typealias Record = [String: JSONValue]

struct UserContext: Decodable {
  let userId: String
  let email: String
  let displayName: String
  let role: String
  let portfolioName: String?
  var isAdmin: Bool { role == "admin" }
}

struct SessionResponse: Decodable {
  let authenticated: Bool
  let context: UserContext?
}

struct DatasetResponse<T: Decodable>: Decodable {
  let payload: T
  let messages: [String]
}

struct TrendPoint: Decodable, Identifiable {
  let date: String
  let value: Double
  let period: String?
  var id: String { date }
}

struct HomeSnapshot: Decodable {
  struct Summary: Decodable {
    let headline: String
    let markdown: String
    let audioAvailable: Bool
  }
  struct Beat: Decodable, Identifiable {
    let id: String
    let kind: String
    let title: String
    let summary: String
    let businessContext: String
    let symbols: [String]
    let tickers: [TickerSnapshot]
  }
  struct Datapoint: Decodable, Identifiable {
    let id: String
    let title: String
    let significance: String
    let label: String
    let unit: String
    let date: String
    let kind: String?
    let period: String?
    let points: [TrendPoint]
  }
  let status: String
  let viewDate: String
  let isReplay: Bool
  let asOf: String
  let runId: String
  let summary: Summary?
  let beats: [Beat]
  let dailyDatapoints: [Datapoint]?
  let dailyDatapointsAsOf: String?
  let graph: JSONValue?
}

struct TickerSnapshot: Decodable, Identifiable {
  struct IntradayHistory: Decodable {
    let status: String
    let points: [TrendPoint]
    let interval: String
    let feed: String
    let session: String
    let asOf: String
    let prefetchedAt: String
    let changePct: Double?
  }
  let symbol: String
  let companyName: String
  let latestClose: Double?
  let priceChange1DPct: Double?
  let priceChange1MPct: Double?
  let marketCap: Double?
  let revenue: Double?
  let revenueYoyPct: Double?
  let netIncome: Double?
  let freeCashFlow: Double?
  let operatingCashFlow: Double?
  let totalAssets: Double?
  let stockholdersEquity: Double?
  let fundamentalsPeriod: String
  let financialTrends: [String: [TrendPoint]]?
  let priceTrend: [TrendPoint]?
  let intradayPriceHistory: IntradayHistory?
  var id: String { symbol }
}

struct MarketCatalog: Decodable {
  struct Lens: Decodable, Identifiable {
    let name: String
    let description: String
    let symbols: [String]
    var id: String { name }
  }
  struct Horizon: Decodable, Identifiable {
    let key: String
    let label: String
    let column: String
    var id: String { key }
  }
  let business: [Lens]
  let commodities: [Lens]
  let horizons: [Horizon]
}

struct PortfolioSnapshot: Decodable {
  struct Portfolio: Decodable { let name: String }
  struct Analytics: Decodable {
    struct Correlation: Decodable {
      let labels: [String]
      let keys: [String]?
      let values: [[Double?]]
      enum CodingKeys: String, CodingKey { case labels, keys, values }
      init(from decoder: Decoder) throws {
        // The existing v1 service uses [] for a portfolio with no equities.
        if let empty = try? decoder.singleValueContainer().decode([JSONValue].self), empty.isEmpty {
          labels = []
          keys = nil
          values = []
          return
        }
        let container = try decoder.container(keyedBy: CodingKeys.self)
        labels = try container.decode([String].self, forKey: .labels)
        keys = try container.decodeIfPresent([String].self, forKey: .keys)
        values = try container.decode([[Double?]].self, forKey: .values)
      }
    }
    let positions: [Record]
    let correlation: Correlation
    let components: [Record]
    let stats: Record
  }
  struct Performance: Decodable {
    let metrics: Record
    let table: [Record]
  }
  let status: String
  let provider: String
  let portfolio: Portfolio
  let summary: Record
  let holdings: [Record]
  let timeseries: [Record]
  let equityAnalytics: Analytics
  let performance: Performance
  let accountPerformance: [Record]
  let opportunitySets: [Record]
  let refresh: Record
  let asOf: String
}

struct PortfolioOpportunitySnapshot: Decodable {
  struct Series: Decodable {
    let groupKey: String
    let points: [TrendPoint]
  }
  let status: String
  let series: [Series]
  let rankings: [Record]
  let contributions: [Record]
  let correlation: PortfolioSnapshot.Analytics.Correlation
  let sets: [Record]
  let returnPeriod: String
  let riskPeriod: String
  let asOf: String
}

struct ResearchRun: Decodable, Identifiable, Hashable {
  static func == (lhs: ResearchRun, rhs: ResearchRun) -> Bool { lhs.runId == rhs.runId }
  func hash(into hasher: inout Hasher) { hasher.combine(runId) }
  struct Result: Decodable {
    struct Citation: Decodable, Identifiable {
      let url: String
      let label: String
      var id: String { url }
    }
    let answerMarkdown: String
    let citations: [Citation]
    let confidence: String?
    let limitations: [String]?
    let model: String?
    let provider: String?
    let error: String
  }
  let runId: String
  let conversationId: String
  let turnId: String
  let query: String
  let status: String
  let result: Result?
  let lastEventSequence: Int
  let updatedAt: String
  var id: String { runId }
  var isTerminal: Bool { ["completed", "failed", "cancelled"].contains(status) }
}

struct ResearchEvent: Decodable, Identifiable {
  struct Payload: Decodable {
    struct Update: Decodable {
      let summary: String
      let finding: String
      let nextStep: String
    }
    let publicUpdate: Update?
    let activityLabel: String?
    let sourceLinks: [ResearchRun.Result.Citation]?
  }
  let eventId: String
  let sequence: Int
  let message: String
  let progress: Double
  let createdAt: String
  let payload: Payload
  var id: String { eventId }
}

struct ResearchEvents: Decodable {
  let events: [ResearchEvent]
  let lastEventSequence: Int
}

struct ResearchConversationSummary: Decodable, Identifiable, Hashable {
  let conversationId: String
  let title: String
  let latestRunId: String
  let status: String
  let turnCount: Int
  let updatedAt: String
  var id: String { conversationId }
}

struct ResearchConversation: Decodable {
  struct Turn: Decodable, Identifiable {
    let turnId: String
    let query: String
    let attempts: [ResearchRun]
    var id: String { turnId }
  }
  let conversationId: String
  let title: String
  let turns: [Turn]
}

struct KnowledgePage: Decodable, Identifiable {
  let pageId: String
  let title: String
  let summary: String
  let bodyMarkdown: String?
  let sourceUrls: [String]?
  let pageType: String?
  let slug: String?
  let version: Int?
  let updatedAt: String?
  let links: [KnowledgePage]?
  let outgoingLinks: [String]?
  let direction: String?
  var id: String { pageId }
}

struct KnowledgeWorkspace: Decodable {
  struct Folder: Decodable, Identifiable {
    let folderId: String
    let name: String
    let path: String
    var id: String { folderId }
  }
  let pages: [KnowledgePage]
  let sourcePages: [KnowledgePage]
  let folders: [Folder]
  let hasMore: Bool
  let folderName: String
  let documents: [Record]
  let proposals: [Record]
  let maintenanceReports: [Record]
  let counts: Record
}
