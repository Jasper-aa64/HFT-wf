#include "tick_engine.hpp"
#include <chrono>

namespace trading {

TickEngine::TickEngine() {}

void TickEngine::process_tick(const Tick& tick) {
    auto start = std::chrono::high_resolution_clock::now();
    
    current_time_ = tick.timestamp;

    // Register symbol → get stable integer ID
    SymbolId sid = SymbolRegistry::instance().register_symbol(tick.symbol);

    // Get or create order book; wire fast-lookup slot on first sight
    if (order_books_.find(tick.symbol) == order_books_.end()) {
        auto ob = std::make_unique<OrderBook>(tick.symbol);
        ob->set_trade_callback([this](const Trade& t) { on_trade(t); });
        OrderBook* raw = ob.get();
        order_books_[tick.symbol] = std::move(ob);

        if (sid >= book_by_id_.size()) book_by_id_.resize(sid + 1, nullptr);
        book_by_id_[sid] = raw;  // O(1) slot — no second find needed
    }
    
    // Notify strategies
    for (auto& strategy : strategies_) {
        strategy->on_tick(tick, this);
    }
    
    auto end = std::chrono::high_resolution_clock::now();
    auto latency = std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();
    
    ++stats_.ticks_processed;
    stats_.total_latency_ns += latency;
}

void TickEngine::submit_order(const Order& order_template) {
    Order* order = order_pool_.allocate();
    *order = order_template;
    order->id = next_order_id_++;
    order->timestamp = current_time_;

    // Route to the correct book via SymbolId — O(1), no string lookup
    SymbolId sid = order->symbol_id;
    if (sid < book_by_id_.size() && book_by_id_[sid] != nullptr) {
        book_by_id_[sid]->add_order(order);
        ++stats_.orders_submitted;
    }
}

OrderId TickEngine::prepare_order(const Order& order_template) {
    OrderId id = next_order_id_++;
    pending_orders_[id] = order_template;
    return id;
}

void TickEngine::submit_prepared_order(OrderId id) {
    auto it = pending_orders_.find(id);
    if (it == pending_orders_.end()) return;

    Order* order = order_pool_.allocate();
    *order = it->second;
    order->id = id;
    order->timestamp = current_time_;

    SymbolId sid = order->symbol_id;
    if (sid < book_by_id_.size() && book_by_id_[sid] != nullptr) {
        book_by_id_[sid]->add_order(order);
        ++stats_.orders_submitted;
    }
    pending_orders_.erase(it);
}

void TickEngine::run_backtest(const std::vector<Tick>& ticks) {
    for (const auto& tick : ticks) {
        process_tick(tick);
    }
}

void TickEngine::add_strategy(std::unique_ptr<Strategy> strategy) {
    strategies_.push_back(std::move(strategy));
}

OrderBook* TickEngine::get_order_book(const std::string& symbol) {
    auto it = order_books_.find(symbol);
    return it != order_books_.end() ? it->second.get() : nullptr;
}

void TickEngine::on_trade(const Trade& trade) {
    ++stats_.trades_executed;
    for (auto& strategy : strategies_) {
        strategy->on_trade(trade);
    }
}

} // namespace trading
