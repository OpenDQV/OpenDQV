"""Tests for the GraphQL API."""



class TestGraphQLQueries:
    def test_list_contracts(self, client):
        query = '{ contracts { name version description ruleCount } }'
        r = client.post("/graphql", json={"query": query})
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data["contracts"]) > 0
        assert any(c["name"] == "customer" for c in data["contracts"])

    def test_get_contract_detail(self, client):
        query = '{ contract(name: "customer") { name version rules { name type field severity } contexts } }'
        r = client.post("/graphql", json={"query": query})
        assert r.status_code == 200
        data = r.json()["data"]["contract"]
        assert data["name"] == "customer"
        assert len(data["rules"]) > 0

    def test_contract_not_found(self, client):
        query = '{ contract(name: "nonexistent") { name } }'
        r = client.post("/graphql", json={"query": query})
        assert r.status_code == 200
        assert r.json()["data"]["contract"] is None


class TestGraphQLMutations:
    def test_validate_single(self, client):
        query = '''
        mutation {
            validate(
                record: {email: "test@example.com", age: 25, name: "Alice", id: "123", phone: "+1234567890", balance: 100, score: 85, date: "2024-01-15", username: "alice_w", password: "securepass1"},
                contract: "customer"
            ) {
                valid
                errors { field rule message severity }
                warnings { field rule message severity }
                contract
                version
            }
        }
        '''
        r = client.post("/graphql", json={"query": query})
        assert r.status_code == 200
        data = r.json()["data"]["validate"]
        assert data["valid"] is True
        assert data["contract"] == "customer"

    def test_validate_invalid(self, client):
        query = '''
        mutation {
            validate(
                record: {email: "bad", age: -5, name: ""},
                contract: "customer"
            ) {
                valid
                errors { field rule message }
            }
        }
        '''
        r = client.post("/graphql", json={"query": query})
        assert r.status_code == 200
        data = r.json()["data"]["validate"]
        assert data["valid"] is False
        assert len(data["errors"]) > 0

    def test_validate_with_record_id(self, client):
        query = '''
        mutation {
            validate(
                record: {email: "a@b.com"},
                contract: "customer",
                recordId: "sf-12345"
            ) {
                valid
                recordId
            }
        }
        '''
        r = client.post("/graphql", json={"query": query})
        data = r.json()["data"]["validate"]
        assert data["recordId"] == "sf-12345"

    def test_validate_batch(self, client):
        query = '''
        mutation {
            validateBatch(
                records: [{email: "a@b.com", age: 25}, {email: "bad", age: -5}],
                contract: "customer"
            ) {
                summary { total passed failed errorCount warningCount }
                results { index valid errors { field message } }
                contract
            }
        }
        '''
        r = client.post("/graphql", json={"query": query})
        assert r.status_code == 200
        data = r.json()["data"]["validateBatch"]
        assert data["summary"]["total"] == 2
        assert data["summary"]["failed"] > 0
        assert len(data["results"]) == 2

    def test_validate_contract_not_found(self, client):
        query = '''
        mutation {
            validate(record: {}, contract: "nonexistent") {
                valid
                errors { field message }
            }
        }
        '''
        r = client.post("/graphql", json={"query": query})
        data = r.json()["data"]["validate"]
        assert data["valid"] is False
        assert any("not found" in e["message"] for e in data["errors"])
