wrk.method = "POST"
wrk.headers["Content-Type"] = "application/json"

local counter = 0
request = function()
    counter = counter + 1
    local body = string.format('{"id": %d, "value": "payload-%d"}', counter % 5000, counter)
    return wrk.format(nil, "/items", nil, body)
end