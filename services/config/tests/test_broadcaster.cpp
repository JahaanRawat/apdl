#include <gtest/gtest.h>
#include "apdl/sse/broadcaster.h"

#include <atomic>
#include <string>
#include <vector>

namespace apdl {
namespace test {

class BroadcasterTest : public ::testing::Test {
protected:
    void SetUp() override {
        broadcaster = std::make_unique<SSEBroadcaster>();
    }

    void TearDown() override {
        broadcaster->stop();
        broadcaster.reset();
    }

    std::unique_ptr<SSEBroadcaster> broadcaster;
};

TEST_F(BroadcasterTest, AddAndRemoveConnection) {
    auto conn_id = broadcaster->addConnection("proj_1", [](const std::string&) {});

    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 1u);
    EXPECT_FALSE(conn_id.empty());

    broadcaster->removeConnection("proj_1", conn_id);
    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 0u);
}

TEST_F(BroadcasterTest, MultipleConnectionsSameProject) {
    auto id1 = broadcaster->addConnection("proj_1", [](const std::string&) {});
    auto id2 = broadcaster->addConnection("proj_1", [](const std::string&) {});
    auto id3 = broadcaster->addConnection("proj_1", [](const std::string&) {});

    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 3u);
    EXPECT_EQ(broadcaster->totalConnectionCount(), 3u);

    broadcaster->removeConnection("proj_1", id2);
    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 2u);
}

TEST_F(BroadcasterTest, ConnectionsAcrossProjects) {
    broadcaster->addConnection("proj_1", [](const std::string&) {});
    broadcaster->addConnection("proj_2", [](const std::string&) {});
    broadcaster->addConnection("proj_3", [](const std::string&) {});

    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 1u);
    EXPECT_EQ(broadcaster->connectionCount("proj_2"), 1u);
    EXPECT_EQ(broadcaster->connectionCount("proj_3"), 1u);
    EXPECT_EQ(broadcaster->totalConnectionCount(), 3u);
}

TEST_F(BroadcasterTest, NonexistentProjectHasZeroConnections) {
    EXPECT_EQ(broadcaster->connectionCount("nonexistent"), 0u);
}

TEST_F(BroadcasterTest, BroadcastToConnections) {
    std::vector<std::string> received;

    broadcaster->addConnection("proj_1", [&received](const std::string& msg) {
        received.push_back(msg);
    });

    broadcaster->broadcast("proj_1", "flag_update", R"({"key":"test","enabled":true})");

    ASSERT_EQ(received.size(), 1u);
    EXPECT_NE(received[0].find("event: flag_update"), std::string::npos);
    EXPECT_NE(received[0].find("data: "), std::string::npos);
    EXPECT_NE(received[0].find("id: "), std::string::npos);
}

TEST_F(BroadcasterTest, BroadcastOnlyToTargetProject) {
    std::atomic<int> proj1_count{0};
    std::atomic<int> proj2_count{0};

    broadcaster->addConnection("proj_1", [&proj1_count](const std::string&) {
        proj1_count.fetch_add(1);
    });

    broadcaster->addConnection("proj_2", [&proj2_count](const std::string&) {
        proj2_count.fetch_add(1);
    });

    broadcaster->broadcast("proj_1", "test_event", "data");

    EXPECT_EQ(proj1_count.load(), 1);
    EXPECT_EQ(proj2_count.load(), 0);
}

TEST_F(BroadcasterTest, BroadcastFanoutToMultipleConnections) {
    std::atomic<int> count{0};

    broadcaster->addConnection("proj_1", [&count](const std::string&) { count++; });
    broadcaster->addConnection("proj_1", [&count](const std::string&) { count++; });
    broadcaster->addConnection("proj_1", [&count](const std::string&) { count++; });

    broadcaster->broadcast("proj_1", "update", "payload");

    EXPECT_EQ(count.load(), 3);
}

TEST_F(BroadcasterTest, BroadcastToNonexistentProjectIsNoOp) {
    // Should not crash
    broadcaster->broadcast("nonexistent", "event", "data");
    EXPECT_EQ(broadcaster->totalConnectionCount(), 0u);
}

TEST_F(BroadcasterTest, DeadConnectionsRemovedOnBroadcast) {
    std::atomic<int> good_count{0};

    broadcaster->addConnection("proj_1", [](const std::string&) {
        throw std::runtime_error("connection dead");
    });

    broadcaster->addConnection("proj_1", [&good_count](const std::string&) {
        good_count++;
    });

    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 2u);

    broadcaster->broadcast("proj_1", "test", "data");

    // Dead connection should be removed
    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 1u);
    EXPECT_EQ(good_count.load(), 1);
}

TEST_F(BroadcasterTest, RemoveNonexistentConnectionIsNoOp) {
    broadcaster->addConnection("proj_1", [](const std::string&) {});
    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 1u);

    // Removing nonexistent connection should not affect existing ones
    broadcaster->removeConnection("proj_1", "does_not_exist");
    EXPECT_EQ(broadcaster->connectionCount("proj_1"), 1u);
}

TEST_F(BroadcasterTest, RemoveFromNonexistentProjectIsNoOp) {
    broadcaster->removeConnection("nonexistent", "some_id");
    EXPECT_EQ(broadcaster->totalConnectionCount(), 0u);
}

TEST_F(BroadcasterTest, StartStopLifecycle) {
    broadcaster->start();
    EXPECT_EQ(broadcaster->totalConnectionCount(), 0u);

    broadcaster->addConnection("proj_1", [](const std::string&) {});
    EXPECT_EQ(broadcaster->totalConnectionCount(), 1u);

    broadcaster->stop();
    EXPECT_EQ(broadcaster->totalConnectionCount(), 0u);
}

TEST_F(BroadcasterTest, DoubleStartIsIdempotent) {
    broadcaster->start();
    broadcaster->start(); // Should not crash or create duplicate threads
    broadcaster->stop();
}

TEST_F(BroadcasterTest, DoubleStopIsIdempotent) {
    broadcaster->start();
    broadcaster->stop();
    broadcaster->stop(); // Should not crash
}

TEST_F(BroadcasterTest, SSEMessageFormat) {
    std::string received;

    broadcaster->addConnection("proj_1", [&received](const std::string& msg) {
        received = msg;
    });

    broadcaster->broadcast("proj_1", "config_change", R"({"hello":"world"})");

    // Verify SSE format: id, event, data lines, followed by empty line
    EXPECT_NE(received.find("id: "), std::string::npos);
    EXPECT_NE(received.find("event: config_change\n"), std::string::npos);
    EXPECT_NE(received.find("data: {\"hello\":\"world\"}\n"), std::string::npos);
    // Should end with \n\n
    EXPECT_EQ(received.substr(received.size() - 2), "\n\n");
}

TEST_F(BroadcasterTest, UniqueConnectionIds) {
    auto id1 = broadcaster->addConnection("proj_1", [](const std::string&) {});
    auto id2 = broadcaster->addConnection("proj_1", [](const std::string&) {});
    auto id3 = broadcaster->addConnection("proj_2", [](const std::string&) {});

    EXPECT_NE(id1, id2);
    EXPECT_NE(id2, id3);
    EXPECT_NE(id1, id3);
}

} // namespace test
} // namespace apdl
