-- Snapshot of the podcasts table (show list). Shows were added straight
-- into the DB, so this is the copy in git. Safe to re-run: skips any
-- rss_url already present.
--   psql -d <db> -f seeds/podcasts.sql

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Fantasy Football Today', 'https://rss.amperwave.net/v2/feed/audacynetwork/38ad78ba12adc2acec34b47b1a455b85', true, 'fantasy'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://rss.amperwave.net/v2/feed/audacynetwork/38ad78ba12adc2acec34b47b1a455b85');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'RotoWire Fantasy Football With Theo Gremminger', 'https://feeds.simplecast.com/D9lea_gL', true, 'fantasy'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/D9lea_gL');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Fantasy Footballers - Fantasy Football Podcast', 'https://feeds.simplecast.com/sw7PGWfw', true, 'fantasy'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/sw7PGWfw');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Locked On Fantasy Football with Fabs & Marcus', 'https://feeds.simplecast.com/GKvIomw5', true, 'fantasy'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/GKvIomw5');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'The Action Network Sports Betting Podcast', 'https://www.omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/390f52fb-437c-4290-bdaa-b3ec011d3fc8/2d9ae039-d4b2-4a37-8a77-b3ec011d3fce/podcast.rss', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://www.omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/390f52fb-437c-4290-bdaa-b3ec011d3fc8/2d9ae039-d4b2-4a37-8a77-b3ec011d3fce/podcast.rss');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Circles Off', 'https://feeds.megaphone.fm/HAMMR1717066516', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.megaphone.fm/HAMMR1717066516');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'VSiN Best Bets', 'https://feeds.simplecast.com/iXBZFZCf', false, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/iXBZFZCf');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Sharp Football Analysis', 'https://feeds.simplecast.com/UiNmc8XS', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/UiNmc8XS');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Ringer Wise Guys', 'https://feeds.megaphone.fm/ringer-gambling-show', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.megaphone.fm/ringer-gambling-show');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'You Better You Bet', 'https://rss.amperwave.net/v2/feed/audacy/0c00ec031c6665734b6cbf5cb3619324', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://rss.amperwave.net/v2/feed/audacy/0c00ec031c6665734b6cbf5cb3619324');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'NFL Best Bets', 'https://feeds.megaphone.fm/nflbestbets', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.megaphone.fm/nflbestbets');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Bet The Board', 'https://www.omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/f976ef47-3c13-49dc-bb19-b4c100fc71b8/68381fd1-b072-4d63-9a06-b4c100fc75eb/podcast.rss', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://www.omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/f976ef47-3c13-49dc-bb19-b4c100fc71b8/68381fd1-b072-4d63-9a06-b4c100fc75eb/podcast.rss');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'The Favorites Sports Betting Podcast', 'https://www.omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/f806745c-9af9-4941-bf75-ae3300346a6c/012b8953-dca4-461c-becc-ae3300346a7a/podcast.rss', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://www.omnycontent.com/d/playlist/e73c998e-6e60-432f-8610-ae210140c5b1/f806745c-9af9-4941-bf75-ae3300346a6c/012b8953-dca4-461c-becc-ae3300346a7a/podcast.rss');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Sports Gambling Podcast', 'https://feeds.simplecast.com/MqvHchJZ', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/MqvHchJZ');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Play of the Day (PlayerProps.ai)', 'https://feeds.blubrry.com/feeds/3722267.xml', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.blubrry.com/feeds/3722267.xml');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Prop Points (VSiN)', 'https://feeds.simplecast.com/bPyImcOV', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/bPyImcOV');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'The Early Edge (CBS SportsLine)', 'https://rss.amperwave.net/v2/feed/audacynetwork/earlyedge', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://rss.amperwave.net/v2/feed/audacynetwork/earlyedge');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'The OddsBreakers', 'https://feeds.simplecast.com/bXKRxtqT', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/bXKRxtqT');

INSERT INTO podcasts (name, rss_url, active, category)
SELECT 'Locked On Bets', 'https://feeds.simplecast.com/yAEOmnFv', true, 'betting'
WHERE NOT EXISTS (SELECT 1 FROM podcasts WHERE rss_url = 'https://feeds.simplecast.com/yAEOmnFv');

